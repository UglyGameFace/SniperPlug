from __future__ import annotations

import re
from typing import Any


PROMO_TEXT_HINTS = (
    "buy more",
    "save up",
    "eligible items",
    "walmart cash",
    "cash rewards",
    "cashrewards",
    "coupon",
    "extra savings",
    "reward",
    "offer",
    "promotion",
    "promo",
)

PROMO_KEY_HINTS = (
    "promotion",
    "promo",
    "offer",
    "savings",
    "reward",
    "incentive",
    "cash",
    "coupon",
)


def extract_walmart_api_value_proof(item: dict[str, Any], *, current_price: float | None = None) -> dict[str, str]:
    """
    Extract value proof only from Walmart API payload fields.

    This intentionally does not scrape the Walmart page or infer from screenshots.
    If Walmart's API does not return the promo/savings text, SniperPlug should not
    pretend it knows it.
    """
    proof: dict[str, str] = {}

    savings, savings_source = _best_api_savings_amount(item)
    if savings is not None and savings > 0:
        proof["apiSavingsAmount"] = f"{savings:.2f}"
        proof["apiSavingsSource"] = savings_source or "api"
        proof["apiValueKind"] = "walmart_api_savings"
        if current_price is not None and current_price > 0:
            proof["apiReferenceFromSavings"] = f"{round(current_price + savings, 2):.2f}"

    promo_texts = _promotion_texts(item)
    if promo_texts:
        joined = " | ".join(promo_texts[:4])
        proof["apiPromotionText"] = joined[:500]
        proof["apiPromotionCount"] = str(len(promo_texts))
        if "apiValueKind" not in proof:
            proof["apiValueKind"] = "walmart_api_promotion"

        cap, cap_source = _best_promo_savings_cap(promo_texts)
        if cap is not None and cap > 0:
            proof["apiPromotionSavingsCap"] = f"{cap:.2f}"
            proof["apiPromotionSavingsSource"] = cap_source or "apiPromotionText"
            if "buy more" in joined.lower():
                proof["apiValueKind"] = "buy_more_save_promo"

    return proof


def _best_api_savings_amount(item: dict[str, Any]) -> tuple[float | None, str | None]:
    best: float | None = None
    best_source: str | None = None

    for key_path, value in _walk_payload(item):
        normalized = key_path.lower().replace("_", "").replace("-", "").replace(".", "")
        if any(blocked in normalized for blocked in ("unitprice", "priceperunit", "shipping")):
            continue

        # Only keys that explicitly mean a savings amount.
        if not any(token in normalized for token in ("savingsamount", "yousave", "saveamount", "savings")):
            continue

        # Avoid mistaking generic promo objects for product markdown unless an amount is present.
        parsed = _price_from_value(value)
        if parsed is None:
            parsed = _first_money_amount(str(value))
        if parsed is None or parsed <= 0:
            continue

        if best is None or parsed > best:
            best = parsed
            best_source = key_path

    return best, best_source


def _promotion_texts(item: dict[str, Any]) -> list[str]:
    found: list[str] = []

    for key_path, value in _walk_payload(item):
        text = _clean_text(value)
        if not text:
            continue

        lowered_text = text.lower()
        normalized_key = key_path.lower().replace("_", "").replace("-", "").replace(".", "")

        text_has_hint = any(hint in lowered_text for hint in PROMO_TEXT_HINTS)
        key_has_hint = any(hint in normalized_key for hint in PROMO_KEY_HINTS)

        # Keep only real promo/value-looking strings. Product titles alone should not qualify.
        if not text_has_hint and not (key_has_hint and any(token in lowered_text for token in ("save", "$", "cash", "coupon", "offer", "eligible"))):
            continue

        # Filter noisy giant blobs.
        if len(text) > 220:
            text = text[:217].rstrip() + "..."

        if text not in found:
            found.append(text)

    return found


def _best_promo_savings_cap(texts: list[str]) -> tuple[float | None, str | None]:
    best: float | None = None
    best_source: str | None = None

    for text in texts:
        lowered = text.lower()
        if not any(term in lowered for term in ("save", "savings", "off", "cash", "coupon")):
            continue

        for amount in _money_amounts(text):
            if amount <= 0:
                continue
            if best is None or amount > best:
                best = amount
                best_source = text[:80]

    return best, best_source


def _walk_payload(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_payload(child, child_prefix)
        return

    if isinstance(value, list):
        for idx, child in enumerate(value):
            child_prefix = f"{prefix}[{idx}]"
            yield from _walk_payload(child, child_prefix)
        return

    yield prefix, value


def _clean_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return ""
    text = " ".join(str(value).split())
    return text.strip()


def _first_money_amount(text: str) -> float | None:
    amounts = _money_amounts(text)
    return amounts[0] if amounts else None


def _money_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for match in re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", str(text)):
        try:
            amounts.append(float(match.group(1)))
        except Exception:
            continue
    return amounts


def _price_from_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("amount", "price", "value", "displayValue", "currencyAmount", "currencyValue"):
            parsed = _float_or_none(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return _float_or_none(value)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if not match:
            return None
        value = match.group(0)
    try:
        return float(value)
    except Exception:
        return None
