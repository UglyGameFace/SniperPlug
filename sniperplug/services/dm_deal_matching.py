from __future__ import annotations

from typing import Any, Iterable

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    DmDealMatchDecision,
    card_search_text,
    walmart_cash_cents,
)
from sniperplug.services.dm_deal_badges import personalize_dm_card_title
from sniperplug.services.jewelry_quality import card_jewelry_rejection_reason
from sniperplug.services.dm_deal_routes import split_dm_route_preferences, verified_card_route
from sniperplug.services.routing import PRICE_GLITCH_ROUTE, route_label
from sniperplug.services.dm_personal_categories import (
    category_key_for_card,
    explicit_flip_min_profit_cents,
    split_category_preferences,
)
from sniperplug.services.dm_product_interests import (
    ProductPreferenceMatch,
    match_product_preferences,
    split_product_preferences,
)
from sniperplug.services.user_wishlist import (
    match_wishlist_entry,
    normalize_wishlist_size,
)


# Personal DMs keep one internal quality floor. It is not a user setting:
# the subscriber-facing controls are the discount floor, the maximum price
# and the daily cap. 78 is the value every stored preference effectively
# used before the setting was retired.
DM_INTERNAL_MIN_SCORE = 78


def match_dm_deal(
    preference: DmDealAlertPreference,
    card: Any,
    *,
    wishlist_items: Iterable[str] | str | None = None,
) -> DmDealMatchDecision:
    """Apply exact-proof personal filters without weakening hard user limits.

    Inclusion is a single union. A deal DMs when it matches ANY layer the
    subscriber picked:

    * the deal's verified route is one of their selected deal types, or
    * a Walmart product interest / brand matches (the product layer is
      Walmart-only; HP/Target delivery retains its existing behavior and is
      reached through routes, categories or the no-picks default), or
    * the deal's category is one of their selected categories, or
    * a wishlist item names this exact product.

    A subscriber with no picks at all stays eligible for everything. Picks
    never veto each other: selecting deal types no longer pauses product or
    category picks.

    Product interests are OR within their group, brands are OR within their
    group, and when both groups are configured a Walmart deal must satisfy
    both groups to count as a product-layer match.

    Hard guards always apply, however a deal matched: exact current/was
    price proof, a positive markdown, the maximum price, the discount
    floor, the internal quality floor, and the wishlist target/size a user
    set on an exact item. A wishlist target or size outranks every
    inclusion layer.

    When the subscriber set a price-mistake profit floor, a deal that
    matches ONLY through the price-mistake route must also clear it: its
    exact spread (was price minus current price) must reach the floor. A
    deal that also matches another layer is not held to the floor.

    An exact-price lead (no verified was-price) is only ever a DM for a
    subscriber whose product or brand interest matches that exact card;
    there is no markdown to rank it by otherwise.
    """

    pref = preference.normalized()
    if not pref.enabled:
        return DmDealMatchDecision(False, "DM alerts are disabled")
    jewelry_reason = card_jewelry_rejection_reason(card)
    if jewelry_reason:
        return DmDealMatchDecision(False, jewelry_reason)

    search_text = card_search_text(card)
    category_key = category_key_for_card(card)
    retailer = str(getattr(card, "retailer", "") or "").strip().lower()
    is_walmart = retailer.startswith("walmart")
    selected_categories = split_category_preferences(pref.categories)
    product_interests, product_brands = split_product_preferences(pref.categories)
    selected_routes = split_dm_route_preferences(pref.categories)
    wishlist_entry = match_wishlist_entry(wishlist_items, search_text)
    wishlist_match = str(wishlist_entry) if wishlist_entry is not None else ""
    wishlist_target_cents = (
        wishlist_entry.target_price_cents if wishlist_entry is not None else None
    )
    wishlist_size = (
        wishlist_entry.size_text if wishlist_entry is not None else None
    )
    has_wishlist_match = bool(wishlist_match)
    flip_floor_cents = explicit_flip_min_profit_cents(pref.categories)

    current_cents = _money_to_cents(
        getattr(card, "api_current_price", None)
        or getattr(card, "current_price", None)
    )
    reference_cents = _money_to_cents(
        getattr(card, "api_reference_price", None)
        or getattr(card, "typical_price", None)
    )
    discount = _as_float(
        getattr(card, "api_discount_percent", None)
        or getattr(card, "discount", None)
    )
    score = _as_int(getattr(card, "score", 0))
    savings_cents = max(0, (reference_cents or 0) - (current_cents or 0))
    cash_cents = walmart_cash_cents(card)

    no_reference_lead = False
    if current_cents is None or reference_cents is None or discount is None:
        if (
            current_cents is not None
            and current_cents > 0
            and reference_cents is None
            and _is_extreme_price_lead(card)
        ):
            # Exact-price lead with no verified was-price: the extreme lane
            # (<= $10 substantial hardware, or a list-price anomaly) or the
            # page-price lane already qualified the price itself, so there is
            # no markdown to measure against the discount/score floors
            # below. The explicit product-interest requirement further down
            # is what keeps this narrow: category-only subscribers never
            # get unproven-price DMs.
            no_reference_lead = True
            discount = 0.0
        else:
            return DmDealMatchDecision(
                False,
                "exact current/was price proof is incomplete",
                category_key,
            )
    if not no_reference_lead and (
        current_cents <= 0 or reference_cents <= current_cents
    ):
        return DmDealMatchDecision(
            False,
            "exact markdown is not positive",
            category_key,
        )

    # These remain hard even when a deal matches an inclusion layer.
    if pref.max_price_cents is not None and current_cents > pref.max_price_cents:
        return DmDealMatchDecision(False, "price is above your maximum", category_key)
    if wishlist_target_cents is not None and current_cents > wishlist_target_cents:
        # The user priced this exact item themselves ("only tell me at or
        # under $X"), so the target outranks every inclusion layer below.
        return DmDealMatchDecision(
            False,
            (
                f"wishlist: {wishlist_match} is ${current_cents / 100:,.2f}, "
                f"above your ${wishlist_target_cents / 100:,.2f} target"
            ),
            category_key,
        )
    if wishlist_size is not None:
        # The user sized this exact item themselves ("only tell me about
        # size 10"), so the size is a hard guard with the same fail-closed
        # philosophy as the target above: the deal's size must be the
        # proven variant size — structured variant proof, never embed
        # text — and an unproven size does not DM at all.
        deal_size = _proven_card_size(card)
        if deal_size != wishlist_size:
            return DmDealMatchDecision(
                False,
                (
                    f"wishlist: {wishlist_match} — you asked for size "
                    f"{wishlist_size}, but this deal's proven size is "
                    f"{deal_size or 'unknown'}"
                ),
                category_key,
            )

    # Inclusion layers. Each is computed independently; any one of them is
    # enough, and none of them can veto another.
    route = verified_card_route(card)
    route_match = route is not None and route in selected_routes

    product_match = ProductPreferenceMatch(False)
    has_product_intent = bool(product_interests or product_brands)
    if is_walmart and has_product_intent:
        product_match = match_product_preferences(
            search_text,
            interests=product_interests,
            brands=product_brands,
        )

    category_match = category_key in selected_categories or (
        cash_cents > 0 and "walmart_cash" in selected_categories
    )

    # An exact-price lead is only ever a DM for a subscriber who named the
    # exact product or brand and matched it. Without that explicit intent
    # there is no markdown to rank it by, so route/category/wishlist-only
    # subscribers stay out.
    if no_reference_lead and not product_match.matched:
        return DmDealMatchDecision(
            False,
            "exact-price leads need an explicit product or brand interest",
            category_key,
        )

    matched_layers: list[str] = []
    if route_match:
        matched_layers.append("route")
    if product_match.matched:
        matched_layers.append("product")
    if category_match:
        matched_layers.append("category")
    if has_wishlist_match:
        matched_layers.append("wishlist")

    # Product picks are a Walmart-only layer: they include Walmart deals
    # and are simply silent for other retailers, never a veto there. So
    # they only count as a "selection" when this card is a Walmart card.
    has_any_selection = bool(
        selected_routes
        or selected_categories
        or (is_walmart and has_product_intent)
    )
    if has_any_selection and not matched_layers:
        if is_walmart and has_product_intent:
            reason = product_match.reason
        elif selected_routes and route is None:
            reason = "verified route metadata unavailable"
        elif selected_routes:
            reason = "deal is not in your selected routes"
        else:
            reason = "category is not selected"
        return DmDealMatchDecision(False, reason, category_key)

    # The price-mistake profit floor only gates deals whose single way in
    # was the price-mistake route. A lead has no verified spread to weigh,
    # and it already needed an explicit product match above, so it is never
    # gated here.
    if (
        flip_floor_cents > 0
        and not no_reference_lead
        and matched_layers == ["route"]
        and route == PRICE_GLITCH_ROUTE
        and savings_cents < flip_floor_cents
    ):
        return DmDealMatchDecision(
            False,
            (
                f"price mistake spread ${savings_cents / 100:,.2f} is below "
                f"your ${flip_floor_cents / 100:,.2f} minimum"
            ),
            category_key,
        )

    if no_reference_lead:
        # No verified markdown exists to measure: the lane already
        # qualified the exact price, and the explicit product interest
        # above is the filter. The cutoffs stay exactly as-is for markdown
        # deals; this path never applies to them.
        required_discount = 0
        required_score = 0
    else:
        required_discount = pref.min_discount
        required_score = DM_INTERNAL_MIN_SCORE

    if discount < required_discount:
        return DmDealMatchDecision(
            False,
            f"{discount:.0f}% is below the required {required_discount}%",
            category_key,
            required_discount,
            savings_cents,
            cash_cents,
        )
    if score < required_score:
        return DmDealMatchDecision(
            False,
            f"score {score} is below the required {required_score}",
            category_key,
            required_discount,
            savings_cents,
            cash_cents,
        )

    if no_reference_lead:
        reason = (
            f"Exact-price lead • ${current_cents / 100:,.2f} • "
            "no verified was-price, no discount claimed • "
            f"category {category_key}"
        )
    else:
        reason = (
            f"{discount:.0f}% exact markdown • saves ${savings_cents / 100:,.2f} • "
            f"score {score}/250 • category {category_key}"
        )
        if cash_cents > 0:
            reason += f" • ${cash_cents / 100:,.2f} Walmart Cash"

    if route_match and route:
        reason += f" • route {route_label(route)}"
    if is_walmart and product_match.reason:
        reason += f" • {product_match.reason}"
    if product_match.matched:
        reason += " • explicit product intent"
    if has_wishlist_match:
        if wishlist_target_cents is not None:
            reason += (
                f" • wishlist: {wishlist_match} • "
                f"${current_cents / 100:,.2f} beat your "
                f"${wishlist_target_cents / 100:,.2f} target"
            )
        else:
            reason += f" • wishlist: {wishlist_match}"
        if wishlist_size is not None:
            reason += f" • size {wishlist_size}"

    if is_walmart:
        personalize_dm_card_title(card)

    return DmDealMatchDecision(
        True,
        reason,
        category_key,
        required_discount,
        savings_cents,
        cash_cents,
    )


def _proven_card_size(card: Any) -> str | None:
    """The deal's proven variant size, normalized — never embed text.

    Reads only the structured variant proof the provider stored on the
    card (``variant_attributes["size"]`` on the card or its deal). A
    size that appears only in a title or an embed field is not proof,
    so it returns None here and the size guard fails closed.
    """

    deal = getattr(card, "deal", None)
    for source in (
        getattr(card, "variant_attributes", None),
        getattr(deal, "variant_attributes", None),
    ):
        if isinstance(source, dict):
            size = normalize_wishlist_size(source.get("size"))
            if size is not None:
                return size
    return None


def _is_extreme_price_lead(card: Any) -> bool:
    """True only for cards the extreme / page-price lane itself built.

    The lane markers are set by ``build_extreme_price_card`` (API-priced
    leads) and ``build_page_price_extreme_card`` (page-observed leads). Any
    other card without a reference price keeps the old exact-proof rejection
    — a missing was-price on an ordinary card is not a lead.
    """

    if bool(getattr(card, "page_price_lead", False)):
        return True
    if bool(getattr(card, "listing_error_lead", False)):
        return True
    if getattr(card, "extreme_exact_candidate", None) is not None:
        return True
    return getattr(card, "page_price_candidate", None) is not None


def _money_to_cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = (
        value.replace("$", "").replace(",", "").strip()
        if isinstance(value, str)
        else str(value)
    )
    try:
        amount = float(text)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return int(round(amount * 100))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
