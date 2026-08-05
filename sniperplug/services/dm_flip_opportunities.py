from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sniperplug.services.opportunity_watchlist import OPPORTUNITY_CATEGORIES


DEFAULT_FLIP_MIN_PROFIT_CENTS = 5_000
MIN_CONFIRMED_SOLD_COMPS = 3
MAX_CONFIRMED_SOLD_WINDOW_DAYS = 90
MIN_ESTIMATED_DISCOUNT = 70.0
MIN_ESTIMATED_REFERENCE_CENTS = 10_000
MIN_ESTIMATED_SAVINGS_CENTS = 7_500
MIN_ESTIMATED_SCORE = 90

_CATEGORY_DEMAND = {
    category.key: int(category.demand_level)
    for category in OPPORTUNITY_CATEGORIES
}

# These categories can still qualify without eBay, but only when the absolute
# retail spread is large enough to avoid percentage-only noise such as cheap
# clothing, pantry items, or disposable household products.
_LOW_VALUE_NOISE_CATEGORIES = {
    "baby_kids",
    "grocery_pantry",
    "health_wellness",
    "household_essentials",
    "pet_supplies",
    "premium_apparel",
    "shoes_apparel",
}

_BULKY_CATEGORIES = {
    "appliances",
    "home_kitchen",
    "outdoor_sports",
}

_BAD_CONDITION_TERMS = (
    "for parts",
    "parts only",
    "damaged",
    "as-is",
    "as is",
    "fair condition",
)


@dataclass(frozen=True)
class FlipAssessment:
    qualified: bool
    confirmed_sold_demand: bool = False
    reason: str = ""
    estimated_profit_cents: int = 0
    median_sold_cents: int = 0
    sold_count: int = 0
    sold_window_days: int = 0


def assess_flip_opportunity(
    card: Any,
    *,
    category_key: str,
    current_cents: int,
    reference_cents: int,
    discount: float,
    savings_cents: int,
    score: int,
    minimum_profit_cents: int = DEFAULT_FLIP_MIN_PROFIT_CENTS,
) -> FlipAssessment:
    """Return a conservative cross-category flip decision.

    Exact product, offer, availability, current-price, and was-price proof are
    enforced by the caller before this assessment runs. Recent eBay sold data
    is accepted only when a future comp provider explicitly marks exact
    identity and condition matching. Active listing prices never count as sold
    evidence.
    """

    minimum_profit = max(1_000, int(minimum_profit_cents))
    attrs = _attributes(card)
    condition_text = _condition_text(attrs)
    if any(term in condition_text for term in _BAD_CONDITION_TERMS):
        return FlipAssessment(False, reason="condition is too risky for a flip override")

    confirmed = _confirmed_ebay_assessment(
        attrs,
        category_key=category_key,
        current_cents=current_cents,
        minimum_profit_cents=minimum_profit,
    )
    if confirmed.qualified:
        return confirmed

    # No verified sold comps are available. Use a large safety haircut instead
    # of pretending Walmart's reference price is an eBay resale price.
    if discount < MIN_ESTIMATED_DISCOUNT:
        return FlipAssessment(False, reason="retail drop is not extreme enough without sold comps")
    if reference_cents < MIN_ESTIMATED_REFERENCE_CENTS:
        return FlipAssessment(False, reason="product value is too low for a significant flip override")
    if savings_cents < MIN_ESTIMATED_SAVINGS_CENTS:
        return FlipAssessment(False, reason="absolute retail spread is too small for a flip override")
    if score < MIN_ESTIMATED_SCORE:
        return FlipAssessment(False, reason="Sniper score is too low for an unconfirmed flip override")

    demand = _CATEGORY_DEMAND.get(category_key, 0)
    if demand < 74 and not (
        reference_cents >= 30_000
        and savings_cents >= 20_000
        and score >= 110
    ):
        return FlipAssessment(False, reason="resale demand is not strong enough without sold comps")

    if category_key in _LOW_VALUE_NOISE_CATEGORIES and not (
        reference_cents >= 20_000
        and savings_cents >= 15_000
        and score >= 100
    ):
        return FlipAssessment(False, reason="category needs a larger absolute spread without sold comps")

    shipping_reserve = _shipping_reserve_cents(category_key)
    # Assume the item resells for only 65% of trusted retail reference, then
    # reserve another 15% for marketplace fees plus category-aware shipping.
    conservative_sale_cents = int(reference_cents * 0.65)
    fee_reserve_cents = int(conservative_sale_cents * 0.15)
    estimated_profit = (
        conservative_sale_cents
        - fee_reserve_cents
        - shipping_reserve
        - current_cents
    )
    roi_percent = (
        (estimated_profit / current_cents) * 100
        if current_cents > 0
        else 0.0
    )

    if estimated_profit < minimum_profit:
        return FlipAssessment(False, reason="conservative estimated profit is below your flip minimum")
    if roi_percent < 35:
        return FlipAssessment(False, reason="conservative estimated ROI is below 35%")

    return FlipAssessment(
        True,
        confirmed_sold_demand=False,
        reason=(
            "🚨 Price-error / flip override • "
            f"{discount:.0f}% exact retail drop • "
            f"conservative estimated net ${estimated_profit / 100:,.2f} • "
            "recent sold comps not connected"
        ),
        estimated_profit_cents=estimated_profit,
    )


def _confirmed_ebay_assessment(
    attrs: dict[str, Any],
    *,
    category_key: str,
    current_cents: int,
    minimum_profit_cents: int,
) -> FlipAssessment:
    identity_matched = _truthy(
        _first(attrs, "ebayCompIdentityMatched", "resaleCompIdentityMatched")
    )
    condition_matched = _truthy(
        _first(attrs, "ebayCompConditionMatched", "resaleCompConditionMatched")
    )
    sold_count = _as_int(
        _first(
            attrs,
            "ebayRecentSoldCount",
            "ebaySoldCompCount",
            "resaleSoldCompCount",
        )
    )
    sold_window_days = _as_int(
        _first(
            attrs,
            "ebaySoldWindowDays",
            "ebayCompWindowDays",
            "resaleSoldWindowDays",
        )
    )
    median_sold_cents = _first_money_cents(
        attrs,
        cents_keys=(
            "ebayMedianSoldPriceCents",
            "ebaySoldMedianPriceCents",
            "resaleMedianSoldPriceCents",
        ),
        money_keys=(
            "ebayMedianSoldPrice",
            "ebaySoldMedianPrice",
            "resaleMedianSoldPrice",
        ),
    )

    if not identity_matched or not condition_matched:
        return FlipAssessment(False, reason="eBay comps are not exact identity/condition matches")
    if sold_count < MIN_CONFIRMED_SOLD_COMPS:
        return FlipAssessment(False, reason="not enough recent eBay sold comps")
    if sold_window_days <= 0 or sold_window_days > MAX_CONFIRMED_SOLD_WINDOW_DAYS:
        return FlipAssessment(False, reason="eBay sold-comp window is missing or stale")
    if median_sold_cents <= 0:
        return FlipAssessment(False, reason="eBay median sold price is missing")

    fee_cents = _first_money_cents(
        attrs,
        cents_keys=("ebayEstimatedFeesCents", "resaleEstimatedFeesCents"),
        money_keys=("ebayEstimatedFees", "resaleEstimatedFees"),
    )
    if fee_cents <= 0:
        fee_cents = int(median_sold_cents * 0.15)

    shipping_cents = _first_money_cents(
        attrs,
        cents_keys=(
            "ebayEstimatedShippingCents",
            "resaleEstimatedShippingCents",
        ),
        money_keys=("ebayEstimatedShipping", "resaleEstimatedShipping"),
    )
    if shipping_cents <= 0:
        shipping_cents = _shipping_reserve_cents(category_key)

    estimated_profit = median_sold_cents - fee_cents - shipping_cents - current_cents
    roi_percent = (
        (estimated_profit / current_cents) * 100
        if current_cents > 0
        else 0.0
    )
    if estimated_profit < minimum_profit_cents:
        return FlipAssessment(False, reason="eBay-comp estimated net profit is below your minimum")
    if roi_percent < 25:
        return FlipAssessment(False, reason="eBay-comp estimated ROI is below 25%")

    return FlipAssessment(
        True,
        confirmed_sold_demand=True,
        reason=(
            "💰 eBay sold-comp flip • "
            f"{sold_count} sold in {sold_window_days}d • "
            f"median ${median_sold_cents / 100:,.2f} • "
            f"estimated net ${estimated_profit / 100:,.2f}"
        ),
        estimated_profit_cents=estimated_profit,
        median_sold_cents=median_sold_cents,
        sold_count=sold_count,
        sold_window_days=sold_window_days,
    )


def _shipping_reserve_cents(category_key: str) -> int:
    if category_key in _BULKY_CATEGORIES:
        return 7_500
    if category_key in {"tools", "outdoor_sports", "baby_kids"}:
        return 4_000
    return 1_500


def _attributes(card: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in (
        getattr(card, "variant_attributes", None),
        getattr(getattr(card, "candidate", None), "variant_attributes", None),
        getattr(getattr(card, "deal", None), "variant_attributes", None),
    ):
        if isinstance(source, dict):
            merged.update(source)
    return merged


def _condition_text(attrs: dict[str, Any]) -> str:
    values = [
        _first(attrs, "condition", "apiCondition", "conditionDescription"),
    ]
    return " ".join(str(value or "").strip().lower() for value in values)


def _first(attrs: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_money_cents(
    attrs: dict[str, Any],
    *,
    cents_keys: tuple[str, ...],
    money_keys: tuple[str, ...],
) -> int:
    for key in cents_keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return max(0, _as_int(value))
    for key in money_keys:
        value = attrs.get(key)
        if value not in (None, ""):
            return _money_to_cents(value)
    return 0


def _money_to_cents(value: Any) -> int:
    if value in (None, ""):
        return 0
    text = (
        value.replace("$", "").replace(",", "").strip()
        if isinstance(value, str)
        else str(value)
    )
    try:
        amount = float(text)
    except (TypeError, ValueError):
        return 0
    return max(0, int(round(amount * 100)))


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "verified",
        "exact",
        "matched",
    }
