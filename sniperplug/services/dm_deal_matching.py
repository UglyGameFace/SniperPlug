from __future__ import annotations

from typing import Any

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    DmDealMatchDecision,
    card_search_text,
    smart_requirements,
    walmart_cash_cents,
)
from sniperplug.services.dm_deal_badges import personalize_dm_card_title
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


def match_dm_deal(
    preference: DmDealAlertPreference,
    card: Any,
) -> DmDealMatchDecision:
    """Apply exact-proof personal filters without weakening hard user limits.

    Specific Walmart product/brand choices are the most explicit user intent.
    When at least one is configured, they become the inclusion layer for Walmart
    and broad category allowlists/mutes do not silently veto the exact product the
    user chose. Without specific product intent, existing category behavior stays
    unchanged. Favorites may still affect Smart-mode priority.

    Product interests are OR within their group, brands are OR within their group,
    and when both groups are configured a Walmart deal must satisfy both groups.
    HP/Target delivery retains its existing behavior.

    Maximum price, Walmart-Cash-only, required/excluded keywords, exact proof,
    minimum markdown/score/savings, dedupe and daily limits remain hard regardless
    of category, product intent or Flip Override.
    """

    pref = preference.normalized()
    if not pref.enabled:
        return DmDealMatchDecision(False, "DM alerts are disabled")

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
    has_specific_walmart_intent = bool(
        is_walmart and (product_interests or product_brands)
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

    if current_cents is None or reference_cents is None or discount is None:
        return DmDealMatchDecision(
            False,
            "exact current/was price proof is incomplete",
            category_key,
        )
    if current_cents <= 0 or reference_cents <= current_cents:
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

    product_match = ProductPreferenceMatch(True)
    if is_walmart:
        product_match = match_product_preferences(
            search_text,
            interests=product_interests,
            brands=product_brands,
        )
        if not product_match.matched:
            return DmDealMatchDecision(
                False,
                product_match.reason,
                category_key,
            )

    flip = FlipAssessment(False)
    if flip_enabled:
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
    # explicitly chooses products/brands, those exact choices win over a stale or
    # overly broad category allowlist/mute. This prevents old category state from
    # crossing into and silently blocking the newer specific-product UX.
    if (
        not has_specific_walmart_intent
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

    if pref.mode == "smart" and not flip.qualified:
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

    if flip.qualified:
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

    if is_walmart and product_match.reason:
        reason += f" • {product_match.reason}"
    if has_specific_walmart_intent:
        reason += " • explicit product intent"

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
