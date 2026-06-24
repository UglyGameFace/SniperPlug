from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


AMOUNT_KEY_HINTS = (
    "amount",
    "value",
    "savings",
    "saving",
    "reward",
    "cash",
    "cashback",
    "cash_back",
    "walmartcash",
    "walmart_cash",
)

BLOCKED_NON_WALMART_CASH_TERMS = (
    "onepay",
    "one pay",
    "credit card",
    "cashrewards",
    "cash rewards",
    "cash back",
    "cashback",
)


@dataclass(frozen=True)
class WalmartCashApiTruth:
    amount: float | None
    proof_path: str
    proof_label: str
    proof_text: str
    raw_value: str

    def as_attributes(self) -> dict[str, str]:
        attrs = {
            "walmartCashApiProof": "yes",
            "walmartCashProofPath": self.proof_path,
            "walmartCashProofLabel": self.proof_label,
            "walmartCashProofText": self.proof_text,
            "walmartCashRawValue": self.raw_value,
        }
        if self.amount is not None:
            attrs["walmartCashSavings"] = f"{self.amount:.2f}"
            attrs["walmartCashAmount"] = f"{self.amount:.2f}"
        return attrs

    def signal(self) -> str:
        amount = f"${self.amount:,.2f}" if self.amount is not None else "eligible / amount not returned"
        return f"Walmart Cash API proof: {amount} from `{self.proof_path}`"


def extract_walmart_cash_api_truth(item: dict[str, Any], *, current_price: float | None) -> WalmartCashApiTruth | None:
    """Find Walmart Cash only from raw Walmart API payload fields.

    This does NOT count OnePay/card cashback, generic rewards, query text,
    category guesses, search terms, or user-visible app screenshots.
    """

    best: WalmartCashApiTruth | None = None

    for path, obj in _walk_dict_objects(item):
        if not path and obj is item:
            # Root object can contain too much unrelated text. Leaf/object paths
            # give cleaner proof and avoid using normal product price as cash.
            continue

        object_text = _object_text(path, obj)
        if not _has_explicit_walmart_cash(object_text):
            continue
        if _is_only_onepay_or_generic_cash(object_text):
            continue

        amount = _amount_from_cash_object(obj, path=path)
        if amount is not None and not _amount_is_sane(amount, current_price=current_price):
            amount = None

        proof = WalmartCashApiTruth(
            amount=amount,
            proof_path=path or "raw_item",
            proof_label=_friendly_label(path),
            proof_text=_clean_preview(object_text, 180),
            raw_value=_clean_preview(obj, 220),
        )
        best = _choose_better(best, proof)

    for path, value in _walk_leaves(item):
        text = f"{path} {value}"
        if not _has_explicit_walmart_cash(text):
            continue
        if _is_only_onepay_or_generic_cash(text):
            continue

        amount = _amount_from_leaf(path, value)
        if amount is not None and not _amount_is_sane(amount, current_price=current_price):
            amount = None

        proof = WalmartCashApiTruth(
            amount=amount,
            proof_path=path,
            proof_label=_friendly_label(path),
            proof_text=_clean_preview(text, 180),
            raw_value=_clean_preview(value, 220),
        )
        best = _choose_better(best, proof)

    return best


def _choose_better(current: WalmartCashApiTruth | None, new: WalmartCashApiTruth) -> WalmartCashApiTruth:
    if current is None:
        return new
    if current.amount is None and new.amount is not None:
        return new
    if current.amount is not None and new.amount is not None and new.amount > current.amount:
        return new
    if len(new.proof_path) < len(current.proof_path):
        return new
    return current


def _has_explicit_walmart_cash(text: str) -> bool:
    lowered = str(text or "").lower()
    normalized = lowered.replace("_", "").replace("-", "").replace(" ", "")
    return "walmartcash" in normalized or "walmart cash" in lowered


def _is_only_onepay_or_generic_cash(text: str) -> bool:
    lowered = str(text or "").lower()
    normalized = lowered.replace("_", "").replace("-", "").replace(" ", "")

    if "walmartcash" in normalized or "walmart cash" in lowered:
        return False

    return any(term in lowered for term in BLOCKED_NON_WALMART_CASH_TERMS)


def _amount_from_cash_object(obj: dict[str, Any], *, path: str) -> float | None:
    # Prefer explicit sibling amount fields inside objects whose path/text says Walmart Cash.
    for key, value in obj.items():
        key_text = str(key)
        normalized = key_text.lower().replace("_", "").replace("-", "")
        if not any(hint.replace("_", "").lower() in normalized for hint in AMOUNT_KEY_HINTS):
            continue
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed

    # Some APIs return text like "Earn $8 Walmart Cash".
    text = _object_text(path, obj)
    return _money_near_walmart_cash(text)


def _amount_from_leaf(path: str, value: Any) -> float | None:
    path_norm = path.lower().replace("_", "").replace("-", "")
    if any(hint.replace("_", "").lower() in path_norm for hint in AMOUNT_KEY_HINTS):
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return _money_near_walmart_cash(f"{path} {value}")


def _money_near_walmart_cash(text: str) -> float | None:
    lowered = str(text or "").lower()
    if "walmart cash" not in lowered and "walmartcash" not in lowered.replace(" ", ""):
        return None

    matches = list(re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", str(text)))
    if not matches:
        return None

    # Prefer the amount nearest the Walmart Cash words.
    cash_index = min(
        [idx for idx in (lowered.find("walmart cash"), lowered.replace(" ", "").find("walmartcash")) if idx >= 0] or [0]
    )
    best = min(matches, key=lambda m: abs(m.start() - cash_index))
    return _float_or_none(best.group(1))


def _amount_is_sane(amount: float | None, *, current_price: float | None) -> bool:
    if amount is None or amount <= 0:
        return False
    if amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return amount <= 200
    # Allows full/free-after-cash style promos but blocks obvious campaign IDs.
    return amount <= max(current_price * 1.10, current_price + 5.00)


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


def _walk_leaves(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_leaves(child, child_prefix)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            yield from _walk_leaves(child, child_prefix)
    else:
        yield prefix, value


def _object_text(path: str, obj: dict[str, Any]) -> str:
    parts = [path]
    for key, value in obj.items():
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key} {value}")
    return " ".join(str(part) for part in parts)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if not match:
            return None
        value = match.group(0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _friendly_label(path: str) -> str:
    safe = path.replace("_", " ").replace(".", " › ")
    safe = re.sub(r"(?<!^)(?=[A-Z])", " ", safe)
    return " ".join(safe.split()) or "Walmart Cash field"


def _clean_preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
