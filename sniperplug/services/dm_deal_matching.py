from __future__ import annotations

from typing import Any, Iterable

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    DmDealMatchDecision,
    card_search_text,
    smart_requirements,
    walmart_cash_cents,
)
from sniperplug.services.dm_deal_badges import personalize_dm_card_title
from sniperplug.services.jewelry_quality import card_jewelry_rejection_reason
from sniperplug.services.dm_deal_routes import split_dm_route_preferences, verified_card_route
from sniperplug.services.routing import route_label
from sniperplug.services.dm_flip_opportunities import (
    FlipAssessment,
    assess_flip_opportunity,
)
from sniperplug.services.dm_personal_categories import (
    category_key_for_card,
    flip_settings,
    muted_category_preferences,
    split_category_preferences,
    split_exclude_terms,
)
from sniperplug.services.dm_product_interests import (
    ProductPreferenceMatch,
    match_product_preferences,
    split_product_preferences,
)
from sniperplug.services.user_wishlist import match_wishlist_items


def match_dm_deal(
    preference: DmDealAlertPreference,
    card: Any,
    *,
    wishlist_items: Iterable[str] | str | None = None,
) -> DmDealMatchDecision:
    """Apply exact-proof personal filters without weakening hard user limits.

    Specific Walmart product/brand choices are the most explicit user intent.
    When at least one is configured, they become the inclusion layer for Walmart
    and broad category allowlists/mutes do not silently veto the exact product the
    user chose. Without specific product intent, existing category behavior stays
    unchanged. Favorites may still affect Smart-mode priority.

    A matching wishlist item (a free-text product name, any retailer) is the
    same kind of explicit intent: it satisfies the inclusion layer on its own,
    so a muted or unselected category cannot veto the exact item the user
    asked for. It never touches a hard guard — exact proof, discount/score/
    savings floors, max price, keywords, dedupe and daily limits all still
    apply, and it never triggers its own scan.

    Product interests are OR within their group, brands are OR within their group,
    and when both groups are configured a Walmart deal must satisfy both groups.
    HP/Target delivery retains its existing behavior.

    Maximum price, Walmart-Cash-only, required/excluded keywords, exact proof,
    minimum markdown/score/savings, dedupe and daily limits remain hard regardless
    of category, product intent, wishlist intent or Flip Override.
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
    keyword_excludes, legacy_muted = split_exclude_terms(
        pref.exclude_keywords
    )
    selected_categories, favorite_categories = split_category_preferences(
        pref.categories
    )
    product_interests, product_brands = split_product_preferences(pref.categories)
    selected_routes = split_dm_route_preferences(pref.categories)
    has_route_intent = bool(selected_routes)
    wishlist_match = match_wishlist_items(wishlist_items, search_text)
    has_wishlist_match = bool(wishlist_match)
    has_specific_walmart_intent = bool(
        not has_route_intent and is_walmart and (product_interests or product_brands)
    )
    stored_muted = muted_category_preferences(pref.categories)
    muted_categories = frozenset((*legacy_muted, *stored_muted))
    flip_enabled, flip_min_profit_cents = flip_settings(pref.categories)
    is_favorite = category_key in favorite_categories

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
            # no markdown to measure against the discount/score/savings
            # floors below. The explicit product-interest requirement further
            # down is what keeps this narrow: category-only subscribers never
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

    # These remain hard even for product intent and Flip Override.
    if pref.max_price_cents is not None and current_cents > pref.max_price_cents:
        return DmDealMatchDecision(False, "price is above your maximum", category_key)
    if pref.walmart_cash_only and cash_cents <= 0:
        return DmDealMatchDecision(
            False,
            "Walmart Cash proof is required",
            category_key,
        )
    if pref.keywords and not any(term in search_text for term in pref.keywords):
        return DmDealMatchDecision(
            False,
            "none of your required keywords matched",
            category_key,
        )
    if keyword_excludes and any(term in search_text for term in keyword_excludes):
        return DmDealMatchDecision(
            False,
            "an excluded keyword matched",
            category_key,
        )

    # A selected deal type is the subscriber's inclusion preference. Old
    # product/category selections cannot silently block an explicit route.
    # Still apply every hard price, discount, proof and keyword guard.
    route = verified_card_route(card) if has_route_intent else None
    if has_route_intent and route not in selected_routes:
        return DmDealMatchDecision(
            False,
            "deal is not in your selected routes" if route else "verified route metadata unavailable",
            category_key,
        )

    product_match = ProductPreferenceMatch(True)
    if is_walmart and not has_route_intent:
        product_match = match_product_preferences(
            search_text,
            interests=product_interests,
            brands=product_brands,
        )
        # A wishlist hit is explicit intent too: it satisfies the product
        # layer even when the curated product/brand picks do not match.
        if not product_match.matched and not has_wishlist_match:
            return DmDealMatchDecision(
                False,
                product_match.reason,
                category_key,
            )

    # An exact-price lead is only ever a DM for a subscriber who named the
    # exact product or brand. Without that explicit intent there is no
    # markdown to rank it by, so category-only subscribers stay out.
    if no_reference_lead and not has_specific_walmart_intent:
        return DmDealMatchDecision(
            False,
            "exact-price leads need an explicit product or brand interest",
            category_key,
        )

    flip = FlipAssessment(False)
    # Flip assessment needs a trusted reference and discount to weigh; a
    # lead has neither, so it can never qualify as a flip override.
    if flip_enabled and not no_reference_lead:
        flip = assess_flip_opportunity(
            card,
            category_key=category_key,
            current_cents=current_cents,
            reference_cents=reference_cents,
            discount=discount,
            savings_cents=savings_cents,
            score=score,
            minimum_profit_cents=flip_min_profit_cents,
        )

    # Broad categories are a fallback inclusion layer. Once a Walmart subscriber
    # explicitly chooses products/brands — or names an exact wishlist item —
    # those exact choices win over a stale or overly broad category
    # allowlist/mute. This prevents old category state from crossing into and
    # silently blocking the newer specific-product UX.
    if (
        not has_specific_walmart_intent
        and not has_wishlist_match
        and not has_route_intent
        and category_key in muted_categories
        and not flip.qualified
    ):
        return DmDealMatchDecision(
            False,
            "category is muted in your personal DMs",
            category_key,
        )
    if (
        not has_specific_walmart_intent
        and not has_wishlist_match
        and not has_route_intent
        and selected_categories
        and category_key not in selected_categories
    ):
        cash_exception = cash_cents > 0 and "walmart_cash" in selected_categories
        if not cash_exception and not flip.qualified:
            return DmDealMatchDecision(
                False,
                "category is not selected",
                category_key,
            )

    required_discount = pref.min_discount
    required_score = pref.min_score
    required_savings = pref.min_savings_cents

    if no_reference_lead:
        # No verified markdown exists to measure: the lane already qualified
        # the exact price, and the explicit product interest above is the
        # filter. The cutoffs stay exactly as-is for markdown deals; this
        # path never applies to them.
        required_discount = 0
        required_score = 0
        required_savings = 0
    elif pref.mode == "smart" and not flip.qualified:
        smart_discount, smart_savings = smart_requirements(current_cents)
        adaptive_discount = max(20, smart_discount)
        adaptive_savings = smart_savings

        if is_favorite:
            adaptive_discount = max(20, adaptive_discount - 5)
            adaptive_savings = max(0, int(round(adaptive_savings * 0.80)))
        elif cash_cents > 0 and discount >= 20:
            adaptive_discount = max(20, adaptive_discount - 5)

        required_discount = max(pref.min_discount, adaptive_discount)
        required_score = max(pref.min_score, 70)
        required_savings = max(pref.min_savings_cents, adaptive_savings)

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
    if savings_cents < required_savings:
        return DmDealMatchDecision(
            False,
            (
                f"saves ${savings_cents / 100:,.2f}, below the required "
                f"${required_savings / 100:,.2f}"
            ),
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
    elif flip.qualified:
        reason = f"{flip.reason} • category {category_key}"
    else:
        reason = (
            f"{discount:.0f}% exact markdown • saves ${savings_cents / 100:,.2f} • "
            f"score {score}/250 • category {category_key}"
        )
        if is_favorite:
            reason += " • favorite-category priority"
        if cash_cents > 0:
            reason += f" • ${cash_cents / 100:,.2f} Walmart Cash"

    if has_route_intent and route:
        reason += f" • route {route_label(route)}"
    if is_walmart and product_match.reason:
        reason += f" • {product_match.reason}"
    if has_specific_walmart_intent:
        reason += " • explicit product intent"
    if has_wishlist_match and not has_route_intent:
        reason += f" • wishlist: {wishlist_match}"

    if is_walmart:
        personalize_dm_card_title(card)

    return DmDealMatchDecision(
        True,
        reason,
        category_key,
        required_discount,
        savings_cents,
        cash_cents,
        priority_override=flip.qualified,
    )


def _is_extreme_price_lead(card: Any) -> bool:
    """True only for cards the extreme / page-price lane itself built.

    The lane markers are set by ``build_extreme_price_card`` (API-priced
    leads) and ``build_page_price_extreme_card`` (page-observed leads). Any
    other card without a reference price keeps the old exact-proof rejection
    — a missing was-price on an ordinary card is not a lead.
    """

    if bool(getattr(card, "page_price_lead", False)):
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
