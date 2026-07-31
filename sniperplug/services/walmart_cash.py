from __future__ import annotations

import re
from typing import Any


WALMART_CASH_AMOUNT_KEYS = {
    "amount",
    "value",
    "cashAmount",
    "cash_amount",
    "rewardAmount",
    "reward_amount",
    "walmartCashAmount",
    "walmart_cash_amount",
    "cashbackAmount",
    "cash_back_amount",
    "savingsAmount",
    "savings_amount",
}

BLOCKED_AMOUNT_KEY_TERMS = (
    "id",
    "sku",
    "upc",
    "gtin",
    "year",
    "date",
    "campaign",
    "percent",
    "percentage",
    "points",
    "quantity",
    "qty",
    "count",
    "item",
    "model",
)


def strict_walmart_promotion_proof(item: dict[str, Any], *, current_price: float | None, coupon_amount: float | None = None) -> dict[str, str]:
    """Return sanitized coupon/Walmart Cash promo attributes for a Walmart API item.

    Walmart Cash is accepted only when the payload has explicit Walmart Cash
    evidence and the amount is sane for the selected item's current price.
    """
    attrs: dict[str, str] = {}
    if coupon_amount and coupon_amount > 0 and promotion_amount_is_sane(coupon_amount, current_price=current_price):
        attrs["couponSavings"] = f"{coupon_amount:.2f}"
    walmart_cash = walmart_cash_amount(item, current_price=current_price)
    if walmart_cash and walmart_cash > 0:
        attrs["walmartCashSavings"] = f"{walmart_cash:.2f}"
    return attrs


def walmart_cash_amount(item: dict[str, Any], *, current_price: float | None) -> float | None:
    """Extract Walmart Cash only from explicit Walmart Cash evidence.

    This intentionally does not accept generic `reward`, `cash`, `savings`, or
    `promo` matches. Those words appear around unrelated IDs and campaign data and
    have already produced fake $20k+ values in cards.
    """
    if current_price is None or current_price <= 0:
        return None

    best: float | None = None
    for path, obj in _walk_dict_objects(item):
        evidence_text = _object_text(path, obj)
        if not _has_explicit_walmart_cash(evidence_text):
            continue

        amount = _amount_from_explicit_walmart_cash_object(obj)
        if amount is None:
            amount = _money_amount_from_explicit_walmart_cash_text(evidence_text)
        if not walmart_cash_amount_is_sane(amount, current_price=current_price):
            continue

        best = max(best or 0.0, amount or 0.0)
    return best


def walmart_cash_amount_is_sane(
    amount: float | None,
    *,
    current_price: float | None,
    allow_missing_price: bool = False,
    missing_price_cap: float = 200.0,
) -> bool:
    """Validate Walmart Cash against the selected product's current price.

    Normal API and public-card callers must provide the exact current price.
    Exact PDP proof may opt into a conservative missing-price cap when Walmart
    exposes the reward but withholds the selected offer price.
    """
    if amount is None or amount <= 0:
        return False
    if amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return bool(allow_missing_price and amount <= max(0.0, float(missing_price_cap)))

    # Real Walmart Cash promos can sometimes cover the whole item value. Allow
    # full-price / tiny-over-price rewards, but block obvious campaign/ID junk.
    return amount <= max(current_price * 1.10, current_price + 5.00)


def promotion_amount_is_sane(amount: float | None, *, current_price: float | None) -> bool:
    """Validate coupon-like values before displaying them on public cards."""
    if amount is None or amount <= 0:
        return False
    if amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return amount < 500
    return amount <= max(current_price * 1.50, current_price + 25.00)


def parse_money_amount(value: Any) -> float | None:
    """Parse one money-like value without interpreting IDs or arbitrary text."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.replace("$", "").replace(",", "").strip()
        money_match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not money_match:
            return None
        value = money_match.group(0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_explicit_walmart_cash(text: str) -> bool:
    normalized = text.lower().replace("_", "").replace("-", "").replace(" ", "")
    return "walmartcash" in normalized


def _amount_from_explicit_walmart_cash_object(obj: dict[str, Any]) -> float | None:
    for key, value in obj.items():
        normalized_key = str(key).replace("-", "_")
        if normalized_key not in WALMART_CASH_AMOUNT_KEYS:
            continue
        if _blocked_amount_key(normalized_key):
            continue
        parsed = parse_money_amount(value)
        if parsed is not None:
            return parsed

    for key, value in obj.items():
        if not isinstance(value, dict):
            continue
        lowered_key = str(key).lower()
        if not any(token in lowered_key for token in ("cash", "reward", "amount", "value")):
            continue
        nested = _amount_from_explicit_walmart_cash_object(value)
        if nested is not None:
            return nested
    return None


def _blocked_amount_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in BLOCKED_AMOUNT_KEY_TERMS)


def _money_amount_from_explicit_walmart_cash_text(text: str) -> float | None:
    if not _has_explicit_walmart_cash(text):
        return None
    match = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None
    return parse_money_amount(match.group(1))


def _walk_dict_objects(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        yield prefix, value
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_dict_objects(child, child_prefix)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            yield from _walk_dict_objects(child, child_prefix)


def _object_text(path: str, obj: dict[str, Any]) -> str:
    parts = [path]
    for key, value in obj.items():
        if isinstance(value, (str, int, float)):
            parts.append(f"{key} {value}")
    return " ".join(str(part) for part in parts)
