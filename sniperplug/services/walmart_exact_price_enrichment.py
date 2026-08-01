from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest


DEFAULT_EXACT_DETAIL_LIMIT = 8
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

    def summary_line(self) -> str:
        return (
            "Exact Walmart detail price checks: "
            f"attempted **{self.attempted}** • exact items refreshed **{self.enriched}** • "
            f"trusted was prices found **{self.references_found}** • "
            f"identity mismatches blocked **{self.identity_mismatches}** • failures **{self.failed}**"
        )


async def enrich_walmart_exact_prices(
    candidates: Iterable[SourceCandidate],
    *,
    provider: Any,
    limit: int = DEFAULT_EXACT_DETAIL_LIMIT,
    concurrency: int = DEFAULT_EXACT_DETAIL_CONCURRENCY,
    timeout_seconds: float = DEFAULT_EXACT_DETAIL_TIMEOUT_SECONDS,
) -> ExactPriceEnrichmentResult:
    """Refresh a bounded set of exact Walmart items before cards are rendered.

    Search responses are useful for discovery, but Walmart's detail response is
    the safer place to bind current price, was price, seller, condition, and the
    selected item ID. Failed or mismatched detail lookups never overwrite the
    original candidate.
    """

    original = list(candidates)
    if not original:
        return ExactPriceEnrichmentResult(candidates=[])

    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    inner = getattr(provider, "inner", provider)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(detail_fetcher) or not callable(candidate_builder):
        return ExactPriceEnrichmentResult(candidates=original, skipped=len(original))

    selected = _select_candidates(original, limit=max(0, int(limit)))
    if not selected:
        return ExactPriceEnrichmentResult(candidates=original, skipped=len(original))

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

    refreshed = list(original)
    enriched = 0
    references_found = 0
    identity_mismatches = 0
    failed = 0
    for index, exact, status in outcomes:
        if status == "identity_mismatch":
            identity_mismatches += 1
            continue
        if status != "enriched" or exact is None:
            failed += 1
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
    )


def _select_candidates(
    candidates: list[SourceCandidate],
    *,
    limit: int,
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
        score = _enrichment_priority(candidate)
        if score <= 0:
            continue
        scored.append((score, index, candidate, item_id))

    scored.sort(key=lambda row: (-row[0], row[1]))
    return [(index, candidate, item_id) for _, index, candidate, item_id in scored[:limit]]


def _enrichment_priority(candidate: SourceCandidate) -> float:
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

    score = 5.0
    if _trusted_reference(candidate) is None:
        score += 70.0
    else:
        score += 10.0

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
    else:
        for key in _PRICE_PROOF_ATTRIBUTE_KEYS:
            merged_attrs.pop(key, None)
        merged_attrs["referencePriceTrusted"] = "no"
        merged_attrs["exactDetailReferenceStatus"] = "missing"

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


def _positive_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
