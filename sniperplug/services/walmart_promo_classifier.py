from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from sniperplug.services.walmart_cash_api_truth import WalmartCashApiTruth, extract_walmart_cash_api_truth


@dataclass(frozen=True)
class PromoEvidence:
    kind: str
    amount: float | None
    proof_path: str
    proof_text: str
    raw_value: str


@dataclass(frozen=True)
class WalmartPromoScan:
    cash: WalmartCashApiTruth | None = None
    cart_promo: PromoEvidence | None = None
    onepay: PromoEvidence | None = None
    markdown: PromoEvidence | None = None
    clearance: PromoEvidence | None = None
    generic: PromoEvidence | None = None
    raw_promo_paths: tuple[str, ...] = ()

    def counts(self) -> dict[str, int]:
        return {
            "walmart_cash": 1 if self.cash else 0,
            "cart_promo": 1 if self.cart_promo else 0,
            "onepay": 1 if self.onepay else 0,
            "markdown": 1 if self.markdown else 0,
            "clearance": 1 if self.clearance else 0,
            "generic_promo": 1 if self.generic else 0,
        }

    def as_attributes(self) -> dict[str, str]:
        attrs: dict[str, str] = {}

        def put(prefix: str, evidence: PromoEvidence | None) -> None:
            if evidence is None:
                return
            attrs[f"{prefix}ApiProof"] = "yes"
            attrs[f"{prefix}ProofPath"] = evidence.proof_path
            attrs[f"{prefix}ProofText"] = evidence.proof_text
            attrs[f"{prefix}RawValue"] = evidence.raw_value
            if evidence.amount is not None:
                attrs[f"{prefix}Amount"] = f"{evidence.amount:.2f}"

        put("cartPromo", self.cart_promo)
        put("onePay", self.onepay)
        put("markdownPromo", self.markdown)
        put("clearancePromo", self.clearance)
        put("genericPromo", self.generic)

        if self.raw_promo_paths:
            attrs["rawPromoProofPaths"] = " | ".join(self.raw_promo_paths[:10])

        return attrs


def classify_walmart_api_promos(item: dict[str, Any], *, current_price: float | None = None) -> WalmartPromoScan:
    """Classify promo signals without confusing them with Walmart Cash.

    Walmart Cash is accepted only through extract_walmart_cash_api_truth(),
    which requires explicit Walmart Cash proof and a sane dollar amount.
    """

    cash = extract_walmart_cash_api_truth(item, current_price=current_price)

    best: dict[str, PromoEvidence] = {}
    promo_paths: list[str] = []

    for path, value in _walk_leaves(item):
        text = f"{path} {value}"
        if _is_promo_related(path, value):
            if path not in promo_paths:
                promo_paths.append(path)

        evidence = _evidence_from_leaf(path, value)
        if evidence is None:
            continue

        old = best.get(evidence.kind)
        if old is None or _evidence_rank(evidence) > _evidence_rank(old):
            best[evidence.kind] = evidence

    # Never let generic promo evidence override a real classified promo.
    generic = best.get("generic_promo")
    if any(best.get(k) for k in ("cart_promo", "onepay", "markdown", "clearance")):
        generic = None

    return WalmartPromoScan(
        cash=cash,
        cart_promo=best.get("cart_promo"),
        onepay=best.get("onepay"),
        markdown=best.get("markdown"),
        clearance=best.get("clearance"),
        generic=generic,
        raw_promo_paths=tuple(promo_paths[:20]),
    )


def classify_walmart_promos(raw_item: dict[str, Any], *, current_price: float | None = None) -> dict[str, list[dict[str, Any]]]:
    """Stable public export for Cash Finder, API probe, and tests.

    This wrapper intentionally delegates to classify_walmart_api_promos() so every
    consumer uses the same API-truth rules. Do not add separate guess logic here;
    Walmart Cash must come from extract_walmart_cash_api_truth(), which requires
    explicit Walmart Cash proof plus a sane dollar amount for the exact product.
    """

    scan = classify_walmart_api_promos(raw_item, current_price=current_price)
    buckets: dict[str, list[dict[str, Any]]] = {
        "walmart_cash": [],
        "cart_promo": [],
        "onepay": [],
        "markdown": [],
        "clearance": [],
        "generic_promo": [],
    }

    if scan.cash is not None:
        buckets["walmart_cash"].append(
            {
                "path": scan.cash.proof_path,
                "key": _leaf_key(scan.cash.proof_path),
                "value": scan.cash.proof_text,
                "amount": scan.cash.amount,
                "proof_label": scan.cash.proof_label,
                "raw_value": scan.cash.raw_value,
            }
        )

    for bucket, evidence in (
        ("cart_promo", scan.cart_promo),
        ("onepay", scan.onepay),
        ("markdown", scan.markdown),
        ("clearance", scan.clearance),
        ("generic_promo", scan.generic),
    ):
        if evidence is not None:
            buckets[bucket].append(_evidence_row(evidence))

    return buckets


def promo_counts_from_scans(scans: list[WalmartPromoScan]) -> dict[str, int]:
    totals = {
        "walmart_cash": 0,
        "cart_promo": 0,
        "onepay": 0,
        "markdown": 0,
        "clearance": 0,
        "generic_promo": 0,
    }
    for scan in scans:
        for key, value in scan.counts().items():
            totals[key] = totals.get(key, 0) + value
    return totals


def _evidence_from_leaf(path: str, value: Any) -> PromoEvidence | None:
    raw_text = " ".join(str(value or "").split())
    text = f"{path} {raw_text}"
    lowered = text.lower()
    norm = _norm(text)
    amount = _money_from_text(text)

    if "onepay" in norm or "one pay" in lowered or "cashrewards" in norm:
        return PromoEvidence("onepay", amount, path, _clean(raw_text or "OnePay cashback signal"), _clean(value))

    if "buy more" in lowered or "save up to" in lowered or "view eligible items" in lowered:
        return PromoEvidence("cart_promo", amount, path, _clean(raw_text or "Buy more/save more cart promo"), _clean(value))

    if "rollback" in lowered or "roll back" in lowered or "wasprice" in norm or "was price" in lowered:
        return PromoEvidence("markdown", amount, path, _clean(raw_text or "Rollback/was-price markdown signal"), _clean(value))

    if "clearance" in lowered:
        return PromoEvidence("clearance", amount, path, _clean(raw_text or "Clearance signal"), _clean(value))

    if _is_promo_related(path, value):
        return PromoEvidence("generic_promo", amount, path, _clean(raw_text or "Generic promo text"), _clean(value))

    return None


def _evidence_rank(evidence: PromoEvidence) -> tuple[int, int]:
    return (1 if evidence.amount is not None else 0, len(evidence.proof_text))


def _is_promo_related(path: str, value: Any) -> bool:
    text = f"{path} {value}".lower()
    norm = _norm(text)
    tokens = (
        "promo",
        "promotion",
        "offer",
        "reward",
        "badge",
        "benefit",
        "coupon",
        "walmartcash",
        "walmart cash",
        "onepay",
        "cashrewards",
        "buy more",
        "save up to",
        "rollback",
        "clearance",
    )
    return any(token.replace(" ", "") in norm or token in text for token in tokens)


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


def _money_from_text(text: str) -> float | None:
    match = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", str(text or ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _evidence_row(evidence: PromoEvidence) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": evidence.proof_path,
        "key": _leaf_key(evidence.proof_path),
        "value": evidence.proof_text,
        "raw_value": evidence.raw_value,
    }
    if evidence.amount is not None:
        row["amount"] = evidence.amount
    return row


def _leaf_key(path: str) -> str:
    if not path:
        return ""
    cleaned = path.replace("]", "").replace("[", ".")
    return cleaned.split(".")[-1]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
