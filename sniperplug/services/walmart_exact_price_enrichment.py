from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest


DEFAULT_EXACT_DETAIL_LIMIT = 24
DEFAULT_EXACT_DETAIL_CONCURRENCY = 4
DEFAULT_EXACT_DETAIL_TIMEOUT_SECONDS = 8.0

_ROUTE_ATTRIBUTE_KEYS = (
    "finderSourceQuery",
    "finderSourceQueries",
    "finderSourcePage",
    "finderSourceSort",
    "finderSourceOrder",
)
_MARKDOWN_TERMS = ("clearance", "rollback", "special buy", "price drop", "reduced price", "deal")
_VALUE_ATTRIBUTE_KEYS = (
    "referenceContextPrice",
    "apiSavingsAmount",
    "apiPromotionSavingsCap",
    "apiPromotionText",
    "couponSavings",
    "walmartCashSavings",
)
_PRICE_PROOF_ATTRIBUTE_KEYS = (
    "referencePriceTrusted",
    "trustedReferencePrice",
    "trustedReferenceSource",
    "referenceContextPrice",
    "referenceContextSource",
    "apiReferencePrice",
    "apiReferencePath",
    "apiDiscountPercent",
    "api_reference_price",
    "api_reference_path",
    "api_discount_percent",
)


@dataclass(frozen=True)
class ExactPriceEnrichmentResult:
    candidates: list[SourceCandidate]
    attempted: int = 0
    enriched: int = 0
    references_found: int = 0
    identity_mismatches: int = 0
    failed: int = 0
    skipped: int = 0
    proofs_blocked: int = 0

    def summary_line(self) -> str:
        return (
            "Exact Walmart detail price checks: "
            f"attempted **{self.attempted}** • exact items refreshed **{self.enriched}** • "
            f"trusted was prices found **{self.references_found}** • "
            f"unverified search references blocked **{self.proofs_blocked}** • "
            f"identity mismatches blocked **{self.identity_mismatches}** • failures **{self.failed}**"
        )


async def enrich_walmart_exact_prices(
    candidates: Iterable[SourceCandidate],
    *,
    provider: Any,
    limit: int = DEFAULT_EXACT_DETAIL_LIMIT,
    concurrency: int = DEFAULT_EXACT_DETAIL_CONCURRENCY,
    timeout_seconds: float = DEFAULT_EXACT_DETAIL_TIMEOUT_SECONDS,
    min_discount: int = 50,
) -> ExactPriceEnrichmentResult:
    """Refresh exact Walmart item proof before cards are rendered.

    Search responses remain useful for discovery, but an unconfirmed search-level
    reference price must never become public Walmart markdown proof. Candidates
    that cannot be checked because of failure, identity mismatch, provider
    availability, or the safety cap keep their current price and identity while
    their search reference/discount proof is quarantined. Observed-price memory
    can still establish a baseline and later prove a same-item price drop.
    """

    original = list(candidates)
    if not original:
        return ExactPriceEnrichmentResult(candidates=[])

    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    inner = getattr(provider, "inner", provider)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(detail_fetcher) or not callable(candidate_builder):
        blocked = _block_reference_proofs(
            original,
            status="provider_unavailable",
            reason="exact Walmart detail provider unavailable",
        )
        return ExactPriceEnrichmentResult(
            candidates=original,
            skipped=len(original),
            proofs_blocked=blocked,
        )

    selected = _select_candidates(
        original,
        limit=max(0, int(limit)),
        min_discount=max(1, int(min_discount)),
    )
    selected_indices = {index for index, _, _ in selected}

    refreshed = list(original)
    proofs_blocked = 0
    for index, candidate in enumerate(refreshed):
        if index in selected_indices:
            continue
        if _has_reference_proof(candidate):
            proofs_blocked += int(
                _block_unverified_reference(
                    candidate,
                    status="skipped_capacity",
                    reason="exact Walmart detail safety cap reached",
                )
            )

    if not selected:
        return ExactPriceEnrichmentResult(
            candidates=refreshed,
            skipped=len(original),
            proofs_blocked=proofs_blocked,
        )

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def refresh(index: int, candidate: SourceCandidate, item_id: str):
        async with semaphore:
            try:
                payload = await asyncio.wait_for(
                    detail_fetcher(item_id),
                    timeout=max(0.1, float(timeout_seconds)),
                )
                exact = candidate_builder(
                    payload,
                    request=ProviderScanRequest(
                        source_key="walmart_exact_detail",
                        query=item_id,
                        max_results=1,
                        metadata={"exact_detail_price_check": "yes"},
                    ),
                )
            except Exception:
                return index, None, "failed"

            if exact is None:
                return index, None, "failed"

            returned_id = _candidate_item_id(exact)
            if returned_id != item_id:
                return index, None, "identity_mismatch"

            return index, _merge_exact_candidate(candidate, exact, item_id=item_id), "enriched"

    outcomes = await asyncio.gather(
        *(refresh(index, candidate, item_id) for index, candidate, item_id in selected)
    )

    enriched = 0
    references_found = 0
    identity_mismatches = 0
    failed = 0
    for index, exact, status in outcomes:
        if status == "identity_mismatch":
            identity_mismatches += 1
            proofs_blocked += int(
                _block_unverified_reference(
                    refreshed[index],
                    status="identity_mismatch",
                    reason="exact Walmart detail returned a different item ID",
                )
            )
            continue
        if status != "enriched" or exact is None:
            failed += 1
            proofs_blocked += int(
                _block_unverified_reference(
                    refreshed[index],
                    status="failed",
                    reason="exact Walmart detail lookup failed or timed out",
                )
            )
            continue
        refreshed[index] = exact
        enriched += 1
        if _trusted_reference(exact) is not None:
            references_found += 1

    return ExactPriceEnrichmentResult(
        candidates=refreshed,
        attempted=len(selected),
        enriched=enriched,
        references_found=references_found,
        identity_mismatches=identity_mismatches,
        failed=failed,
        skipped=max(0, len(original) - len(selected)),
        proofs_blocked=proofs_blocked,
    )


def _select_candidates(
    candidates: list[SourceCandidate],
    *,
    limit: int,
    min_discount: int,
) -> list[tuple[int, SourceCandidate, str]]:
    if limit <= 0:
        return []

    scored: list[tuple[float, int, SourceCandidate, str]] = []
    seen_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        item_id = _candidate_item_id(candidate)
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        score = _enrichment_priority(candidate, min_discount=min_discount)
        if score <= 0:
            continue
        scored.append((score, index, candidate, item_id))

    scored.sort(key=lambda row: (-row[0], row[1]))
    return [(index, candidate, item_id) for _, index, candidate, item_id in scored[:limit]]


def _enrichment_priority(candidate: SourceCandidate, *, min_discount: int) -> float:
    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    if current is None:
        return 0.0

    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    signals = " ".join(str(value or "") for value in getattr(candidate, "signals", ()) or ())
    routes = " ".join(str(attrs.get(key) or "") for key in _ROUTE_ATTRIBUTE_KEYS)
    haystack = f"{signals} {routes}".lower()

    reference = _trusted_reference(candidate)
    score = 5.0
    if reference is not None:
        discount = _percent_off(current, reference)
        if discount is not None and discount >= max(1, int(min_discount)):
            score += 300.0
        else:
            score += 180.0
    else:
        score += 70.0

    if any(term in haystack for term in _MARKDOWN_TERMS):
        score += 35.0
    if any(str(attrs.get(key) or "").strip() for key in _VALUE_ATTRIBUTE_KEYS):
        score += 25.0
    if str(attrs.get("clearance") or "").lower() == "yes":
        score += 20.0
    if str(attrs.get("rollback") or "").lower() == "yes":
        score += 20.0
    if str(attrs.get("specialBuy") or "").lower() == "yes":
        score += 15.0

    return score


def _merge_exact_candidate(
    original: SourceCandidate,
    exact: SourceCandidate,
    *,
    item_id: str,
) -> SourceCandidate:
    original_attrs = dict(getattr(original, "variant_attributes", None) or {})
    exact_attrs = dict(getattr(exact, "variant_attributes", None) or {})
    for key in _PRICE_PROOF_ATTRIBUTE_KEYS:
        original_attrs.pop(key, None)
    merged_attrs = {**original_attrs, **exact_attrs}
    for key in _ROUTE_ATTRIBUTE_KEYS:
        if original_attrs.get(key) not in (None, ""):
            merged_attrs[key] = original_attrs[key]

    merged_attrs["exactDetailPriceProof"] = "yes"
    merged_attrs["exactDetailItemId"] = item_id
    if getattr(exact, "api_price_path", None):
        merged_attrs["exactDetailCurrentSource"] = str(exact.api_price_path)
    if getattr(exact, "api_reference_path", None):
        merged_attrs["exactDetailReferenceSource"] = str(exact.api_reference_path)
        merged_attrs["exactDetailReferenceStatus"] = "trusted"
        merged_attrs["referencePriceTrusted"] = "yes"
        merged_attrs.pop("observedPriceFallback", None)
    else:
        for key in _PRICE_PROOF_ATTRIBUTE_KEYS:
            merged_attrs.pop(key, None)
        merged_attrs["referencePriceTrusted"] = "no"
        merged_attrs["exactDetailReferenceStatus"] = "missing"
        merged_attrs["observedPriceFallback"] = "exact_item_baseline"

    exact.variant_attributes = merged_attrs
    exact.candidate_id = original.candidate_id
    exact.first_seen_at = original.first_seen_at
    if not exact.image_url:
        exact.image_url = original.image_url
    if not exact.product_url:
        exact.product_url = original.product_url
    if not exact.direct_product_url:
        exact.direct_product_url = original.direct_product_url

    merged_signals: list[str] = []
    for signal in (
        *list(getattr(exact, "signals", ()) or ()),
        f"exact Walmart detail item verified: {item_id}",
        *list(getattr(original, "signals", ()) or ()),
    ):
        text = str(signal or "").strip()
        if text and text not in merged_signals:
            merged_signals.append(text)
    exact.signals = merged_signals[:24]
    return exact


def _block_reference_proofs(
    candidates: Iterable[SourceCandidate],
    *,
    status: str,
    reason: str,
) -> int:
    blocked = 0
    for candidate in candidates:
        if _has_reference_proof(candidate):
            blocked += int(
                _block_unverified_reference(candidate, status=status, reason=reason)
            )
    return blocked


def _block_unverified_reference(
    candidate: SourceCandidate,
    *,
    status: str,
    reason: str,
) -> bool:
    had_reference = _has_reference_proof(candidate)
    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    for key in _PRICE_PROOF_ATTRIBUTE_KEYS:
        attrs.pop(key, None)

    candidate.typical_price = None
    candidate.api_reference_price = None
    candidate.api_reference_path = None
    candidate.api_discount_percent = None

    attrs["referencePriceTrusted"] = "no"
    attrs["exactDetailPriceProof"] = "no"
    attrs["exactDetailReferenceStatus"] = status
    item_id = _candidate_item_id(candidate)
    if item_id:
        attrs["exactDetailItemId"] = item_id
        attrs["observedPriceFallback"] = "exact_item_baseline"
    candidate.variant_attributes = attrs

    signal = f"Walmart search reference blocked: {reason}"
    signals = list(getattr(candidate, "signals", ()) or ())
    if signal not in signals:
        signals.append(signal)
    candidate.signals = signals[:24]
    return had_reference


def _has_reference_proof(candidate: Any) -> bool:
    if _trusted_reference(candidate) is not None:
        return True
    if _positive_number(getattr(candidate, "api_discount_percent", None)) is not None:
        return True
    if str(getattr(candidate, "api_reference_path", None) or "").strip():
        return True

    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    if str(attrs.get("referencePriceTrusted") or "").strip().lower() == "yes":
        return True
    proof_value_keys = (
        "trustedReferencePrice",
        "trustedReferenceSource",
        "referenceContextPrice",
        "referenceContextSource",
        "apiReferencePrice",
        "apiReferencePath",
        "apiDiscountPercent",
        "api_reference_price",
        "api_reference_path",
        "api_discount_percent",
    )
    return any(str(attrs.get(key) or "").strip() for key in proof_value_keys)


def _candidate_item_id(candidate: Any) -> str | None:
    for value in (
        getattr(candidate, "product_id", None),
        getattr(candidate, "sku", None),
        getattr(candidate, "selected_offer_id", None),
    ):
        text = str(value or "").strip()
        if text.isdigit():
            return text
    return None


def _trusted_reference(candidate: Any) -> float | None:
    for value in (
        getattr(candidate, "api_reference_price", None),
        getattr(candidate, "typical_price", None),
    ):
        parsed = _positive_number(value)
        if parsed is not None:
            return parsed
    return None


def _percent_off(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or reference <= current or reference <= 0:
        return None
    return (reference - current) / reference * 100.0


def _positive_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
