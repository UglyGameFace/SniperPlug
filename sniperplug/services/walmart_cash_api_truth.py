from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


# True Walmart Cash proof must come from a Walmart Cash-specific API key/path
# or a promo/reward/badge object that explicitly says Walmart Cash AND includes
# a sane money amount.
WALMART_CASH_KEY_MARKERS = (
    "walmartcash",
    "walmart_cash",
    "walmart cash",
)

PROMO_CONTEXT_MARKERS = (
    "promo",
    "promotion",
    "offer",
    "offers",
    "reward",
    "rewards",
    "incentive",
    "badge",
    "badges",
    "benefit",
    "benefits",
    "savings",
)

REJECT_CONTEXT_MARKERS = (
    "query",
    "search",
    "request",
    "input",
    "url",
    "uri",
    "canonical",
    "producturl",
    "title",
    "name",
    "description",
)

BLOCKED_NON_WALMART_CASH_TERMS = (
    "onepay",
    "one pay",
    "credit card",
    "cashrewards",
    "cash rewards",
    "cash back",
    "cashback",
    "buy more",
    "save up to",
    "view eligible items",
)


@dataclass(frozen=True)
class WalmartCashApiTruth:
    amount: float
    proof_path: str
    proof_label: str
    proof_text: str
    raw_value: str

    def as_attributes(self) -> dict[str, str]:
        return {
            "walmartCashApiProof": "yes",
            "walmartCashSavings": f"{self.amount:.2f}",
            "walmartCashAmount": f"{self.amount:.2f}",
            "walmartCashProofPath": self.proof_path,
            "walmartCashProofLabel": self.proof_label,
            "walmartCashProofText": self.proof_text,
            "walmartCashRawValue": self.raw_value,
            "walmartCashProofMode": "strict_api_field_amount",
        }

    def signal(self) -> str:
        return f"Walmart Cash API proof: ${self.amount:,.2f} from `{self.proof_path}`"


def extract_walmart_cash_api_truth(item: dict[str, Any], *, current_price: float | None) -> WalmartCashApiTruth | None:
    """Return Walmart Cash only when raw Walmart API data proves it.

    This intentionally rejects:
    - search/query text
    - product titles/descriptions
    - OnePay/card cashback
    - generic cashback/rewards
    - Buy more/save up to promos
    - eligibility with no returned cash amount
    """

    best: WalmartCashApiTruth | None = None

    for path, obj in _walk_dict_objects(item):
        if not path:
            continue

        proof_context = _proof_context(path, obj)
        if proof_context is None:
            continue

        text = _object_text(path, obj)
        if _blocked_non_cash_text(text):
            continue

        amount = _amount_from_cash_object(obj, path=path, text=text)
        if not _amount_is_sane(amount, current_price=current_price):
            continue

        proof = WalmartCashApiTruth(
            amount=float(amount),
            proof_path=path,
            proof_label=_friendly_label(path),
            proof_text=_clean_preview(text, 220),
            raw_value=_clean_preview(obj, 260),
        )
        best = _choose_better(best, proof)

    for path, value in _walk_leaves(item):
        proof_context = _leaf_proof_context(path, value)
        if proof_context is None:
            continue

        text = f"{path} {value}"
        if _blocked_non_cash_text(text):
            continue

        amount = _amount_from_leaf(path, value, text=text)
        if not _amount_is_sane(amount, current_price=current_price):
            continue

        proof = WalmartCashApiTruth(
            amount=float(amount),
            proof_path=path,
            proof_label=_friendly_label(path),
            proof_text=_clean_preview(text, 220),
            raw_value=_clean_preview(value, 260),
        )
        best = _choose_better(best, proof)

    return best


def _proof_context(path: str, obj: dict[str, Any]) -> str | None:
    path_norm = _norm(path)
    key_text = " ".join(str(k) for k in obj.keys())
    key_norm = _norm(key_text)
    object_text = _object_text(path, obj).lower()

    explicit_field = any(marker.replace(" ", "") in path_norm or marker.replace(" ", "") in key_norm for marker in WALMART_CASH_KEY_MARKERS)
    explicit_text = "walmart cash" in object_text or "walmartcash" in _norm(object_text)
    promo_context = any(marker in path_norm or marker in key_norm for marker in PROMO_CONTEXT_MARKERS)
    rejected_context = any(marker in path_norm for marker in REJECT_CONTEXT_MARKERS)

    if explicit_field:
        return "explicit_field"

    if explicit_text and promo_context and not rejected_context:
        return "promo_text"

    return None


def _leaf_proof_context(path: str, value: Any) -> str | None:
    path_norm = _norm(path)
    text = f"{path} {value}".lower()
    explicit_field = any(marker.replace(" ", "") in path_norm for marker in WALMART_CASH_KEY_MARKERS)
    explicit_text = "walmart cash" in text or "walmartcash" in _norm(text)
    promo_context = any(marker in path_norm for marker in PROMO_CONTEXT_MARKERS)
    rejected_context = any(marker in path_norm for marker in REJECT_CONTEXT_MARKERS)

    if explicit_field:
        return "explicit_field"

    if explicit_text and promo_context and not rejected_context:
        return "promo_text"

    return None


def _blocked_non_cash_text(text: str) -> bool:
    lowered = str(text or "").lower()
    normalized = _norm(lowered)

    # Walmart Cash-specific text is still allowed, but buy-more/OnePay/card
    # promos must never be upgraded into Walmart Cash.
    if any(term in lowered for term in ("onepay", "one pay", "credit card", "cashrewards", "cash rewards")):
        return True

    if "walmartcash" not in normalized and "walmart cash" not in lowered:
        return any(term in lowered for term in BLOCKED_NON_WALMART_CASH_TERMS)

    # These are separate API Promo lane signals, not Walmart Cash.
    if "buy more" in lowered or "save up to" in lowered or "view eligible items" in lowered:
        return True

    return False


def _amount_from_cash_object(obj: dict[str, Any], *, path: str, text: str) -> float | None:
    best: float | None = None

    for key, value in obj.items():
        key_norm = _norm(key)
        path_norm = _norm(path)
        is_cash_key = "walmartcash" in key_norm or "walmartcash" in path_norm
        is_amount_key = any(token in key_norm for token in ("amount", "value", "savings", "saving", "reward", "cash"))

        if not (is_cash_key or is_amount_key):
            continue

        parsed = _float_or_none(value)
        if parsed is not None and (best is None or parsed > best):
            best = parsed

    nearby = _money_near_walmart_cash(text)
    if nearby is not None and (best is None or nearby > best):
        best = nearby

    return best


def _amount_from_leaf(path: str, value: Any, *, text: str) -> float | None:
    path_norm = _norm(path)
    if "walmartcash" in path_norm or any(token in path_norm for token in ("amount", "value", "savings", "saving", "reward", "cash")):
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed

    return _money_near_walmart_cash(text)


def _money_near_walmart_cash(text: str) -> float | None:
    raw = str(text or "")
    lowered = raw.lower()
    normalized = _norm(lowered)

    if "walmart cash" not in lowered and "walmartcash" not in normalized:
        return None

    matches = list(re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", raw))
    if not matches:
        return None

    cash_positions = [idx for idx in (lowered.find("walmart cash"), normalized.find("walmartcash")) if idx >= 0]
    cash_index = min(cash_positions or [0])
    best = min(matches, key=lambda m: abs(m.start() - cash_index))
    return _float_or_none(best.group(1))


def _amount_is_sane(amount: float | None, *, current_price: float | None) -> bool:
    if amount is None or amount <= 0:
        return False
    if amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return amount <= 200
    return amount <= max(float(current_price) * 1.10, float(current_price) + 5.00)


def _choose_better(current: WalmartCashApiTruth | None, new: WalmartCashApiTruth) -> WalmartCashApiTruth:
    if current is None:
        return new
    if new.amount > current.amount:
        return new
    if len(new.proof_path) < len(current.proof_path):
        return new
    return current


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
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", "").replace("$", ""))
        if not match:
            return None
        value = match.group(0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _friendly_label(path: str) -> str:
    safe = path.replace("_", " ").replace(".", " › ")
    safe = re.sub(r"(?<!^)(?=[A-Z])", " ", safe)
    return " ".join(safe.split()) or "Walmart Cash field"


def _clean_preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# Walmart Cash proof guard:
# Generic rewards, OnePay cashback, card cashback, search words, guessed promos,
# and generic promo text do not count as Walmart Cash proof.
