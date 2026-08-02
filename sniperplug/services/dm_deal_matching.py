from __future__ import annotations

from typing import Any

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    DmDealMatchDecision,
    card_search_text,
    card_title,
    smart_requirements,
    walmart_cash_cents,
)
from sniperplug.services.opportunity_watchlist import category_for_title


def match_dm_deal(
    preference: DmDealAlertPreference,
    card: Any,
) -> DmDealMatchDecision:
    """Apply exact-proof personal filters without weakening user minimums.

    Smart mode may add stricter adaptive requirements based on the item's
    current price. It never lowers a user's explicit minimum markdown, score,
    or dollar-savings floor. Strict API-proven Walmart Cash may soften only the
    adaptive markdown add-on by five points, never the user's own floor and
    never below a real 20% markdown.
    """

    pref = preference.normalized()
    if not pref.enabled:
        return DmDealMatchDecision(False, "DM alerts are disabled")

    title = card_title(card)
    search_text = card_search_text(card)
    category = category_for_title(title)
    category_key = category.key if category is not None else "uncategorized"

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
    if pref.max_price_cents is not None and current_cents > pref.max_price_cents:
        return DmDealMatchDecision(False, "price is above your maximum", category_key)
    if pref.walmart_cash_only and cash_cents <= 0:
        return DmDealMatchDecision(
            False,
            "Walmart Cash proof is required",
            category_key,
        )
    if pref.categories and category_key not in pref.categories:
        if not (cash_cents > 0 and "walmart_cash" in pref.categories):
            return DmDealMatchDecision(
                False,
                "category is not selected",
                category_key,
            )
    if pref.keywords and not any(term in search_text for term in pref.keywords):
        return DmDealMatchDecision(
            False,
            "none of your required keywords matched",
            category_key,
        )
    if pref.exclude_keywords and any(
        term in search_text for term in pref.exclude_keywords
    ):
        return DmDealMatchDecision(
            False,
            "an excluded keyword matched",
            category_key,
        )

    required_discount = pref.min_discount
    required_score = pref.min_score
    required_savings = pref.min_savings_cents

    if pref.mode == "smart":
        smart_discount, smart_savings = smart_requirements(current_cents)
        adaptive_discount = max(20, smart_discount)
        if cash_cents > 0 and discount >= 20:
            adaptive_discount = max(20, adaptive_discount - 5)
        required_discount = max(pref.min_discount, adaptive_discount)
        required_score = max(pref.min_score, 70)
        required_savings = max(pref.min_savings_cents, smart_savings)

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

    reason = (
        f"{discount:.0f}% exact markdown • saves ${savings_cents / 100:,.2f} • "
        f"score {score}/250 • category {category_key}"
    )
    if cash_cents > 0:
        reason += f" • ${cash_cents / 100:,.2f} Walmart Cash"
    return DmDealMatchDecision(
        True,
        reason,
        category_key,
        required_discount,
        savings_cents,
        cash_cents,
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
