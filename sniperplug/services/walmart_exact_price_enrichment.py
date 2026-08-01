from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity


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
_MARKDOWN_TERMS = (
    "clearance",
    "rollback",
    "special buy",
    "price drop",
    "reduced price",
    "deal",
)
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
    offer_identity_blocked: int = 0
    failed: int = 0
    skipped: int = 0
    proofs_blocked: int = 0

    def summary_line(self) -> str:
        return (
            "Exact Walmart detail price checks: "
            f"attempted **{self.attempted}** • exact items refreshed **{self.enriched}** • "
            f"trusted was prices found **{self.references_found}** • "
            f"unverified search references blocked **{self.proofs_blocked}** • "
            f"identity mismatches blocked **{self.identity_mismatches}** • "
            f"incomplete exact-offer identities blocked **{self.offer_identity_blocked}** • "
            f"failures **{self.failed}**"
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
    """Replace search-level pricing with exact Walmart item-detail proof.

    Search is discovery only. A numeric Walmart reference price is trusted only
    when the exact detail response resolves to the requested item ID. A source
    path string without a numeric reference is never sufficient proof. The raw
    exact-detail payload is also used to bind seller/marketplace and offer
    identity before a candidate can surface or train price memory.
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

            _apply_exact_payload_offer_identity(exact, payload=payload, item_id=item_id)
            merged = _merge_exact_candidate(candidate, exact, item_id=item_id)
            if exact_offer_identity(merged) is None:
                attrs = dict(getattr(merged, "variant_attributes", None) or {})
                attrs["exactDetailOfferIdentityStatus"] = "blocked"
                merged.variant_attributes = attrs
                return index, merged, "offer_identity_missing"
            return index, merged, "enriched"

    outcomes = await asyncio.gather(
        *(refresh(index, candidate, item_id) for index, candidate, item_id in selected)
    )

    enriched = 0
    references_found = 0
    identity_mismatches = 0
    offer_identity_blocked = 0
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
        if status == "offer_identity_missing" and exact is not None:
            refreshed[index] = exact
            enriched += 1
            offer_identity_blocked += 1
            if _trusted_reference(exact) is not None:
                references_found += 1
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
        offer_identity_blocked=offer_identity_blocked,
        failed=failed,
        skipped=max(0, len(original) - len(selected)),
        proofs_blocked=proofs_blocked,
    )


def exact_detail_verified_candidates(
    candidates: Iterable[SourceCandidate],
) -> list[SourceCandidate]:
    """Return only official exact-detail candidates with full offer identity."""

    verified: list[SourceCandidate] = []
    for candidate in candidates:
        attrs = dict(getattr(candidate, "variant_attributes", None) or {})
        requested_id = _candidate_item_id(candidate)
        exact_id = str(attrs.get("exactDetailItemId") or "").strip()
        if not (
            str(attrs.get("exactDetailPriceProof") or "").strip().lower() == "yes"
            and requested_id is not None
            and exact_id == requested_id
            and _positive_number(
                getattr(candidate, "api_current_price", None)
                or getattr(candidate, "current_price", None)
            )
            is not None
        ):
            continue
        if exact_offer_identity(candidate) is None:
            continue
        verified.append(candidate)
    return verified


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
    signals = " ".join(
        str(value or "") for value in getattr(candidate, "signals", ()) or ()
    )
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


def _apply_exact_payload_offer_identity(
    exact: SourceCandidate,
    *,
    payload: Any,
    item_id: str,
) -> None:
    """Fill exact seller/offer proof from Walmart's raw detail payload.

    The Affiliate and catalog payloads use both `marketplace` and
    `isMarketPlaceItem` spellings. Missing seller fields on an explicitly
    non-marketplace item mean Walmart is the seller. Marketplace/unknown rows
    without a seller stay incomplete and fail closed.
    """

    item = payload if isinstance(payload, dict) else {}
    attrs = dict(getattr(exact, "variant_attributes", None) or {})

    seller_name = _first_text(
        getattr(exact, "seller_name", None),
        attrs.get("seller"),
        _path_value(item, "sellerName"),
        _path_value(item, "sellerDisplayName"),
        _path_value(item, "seller", "name"),
        _path_value(item, "sellerInfo", "sellerName"),
        _path_value(item, "sellerInfo", "name"),
        _path_value(item, "selectedOffer", "sellerName"),
        _path_value(item, "selectedOffer", "seller", "name"),
        _path_value(item, "buyBoxOffer", "sellerName"),
        _path_value(item, "buyBoxOffer", "seller", "name"),
    )
    seller_id = _first_text(
        attrs.get("sellerId"),
        _path_value(item, "sellerId"),
        _path_value(item, "sellerID"),
        _path_value(item, "seller", "id"),
        _path_value(item, "sellerInfo", "sellerId"),
        _path_value(item, "selectedOffer", "sellerId"),
        _path_value(item, "selectedOffer", "seller", "id"),
        _path_value(item, "buyBoxOffer", "sellerId"),
        _path_value(item, "buyBoxOffer", "seller", "id"),
    )
    marketplace = _first_explicit_bool(
        _path_value(item, "isMarketPlaceItem"),
        _path_value(item, "isMarketplaceItem"),
        _path_value(item, "marketplace"),
        _path_value(item, "selectedOffer", "isMarketPlaceItem"),
        _path_value(item, "selectedOffer", "marketplace"),
        attrs.get("isMarketPlaceItem"),
        attrs.get("isMarketplaceItem"),
        attrs.get("marketplace"),
    )

    explicit_offer_id = _first_text(
        _path_value(item, "offerId"),
        _path_value(item, "offerID"),
        _path_value(item, "selectedOfferId"),
        _path_value(item, "buyBoxOfferId"),
        _path_value(item, "selectedOffer", "offerId"),
        _path_value(item, "selectedOffer", "id"),
        _path_value(item, "buyBoxOffer", "offerId"),
        _path_value(item, "buyBoxOffer", "id"),
    )
    existing_offer_id = _first_text(getattr(exact, "selected_offer_id", None))
    offer_id = explicit_offer_id or existing_offer_id or item_id
    exact.selected_offer_id = offer_id
    attrs["exactDetailOfferId"] = offer_id
    attrs["exactDetailOfferIdentityStatus"] = "verified"
    attrs["exactDetailOfferIdentitySource"] = (
        "payload.offerId" if explicit_offer_id else "exact itemId fallback"
    )

    if marketplace is not None:
        attrs["isMarketPlaceItem"] = "yes" if marketplace else "no"
        attrs["marketplace"] = "yes" if marketplace else "no"

    walmart_seller = _seller_is_walmart(seller_name=seller_name, seller_id=seller_id)
    if not seller_name and not seller_id and marketplace is False:
        seller_name = "Walmart"
        walmart_seller = True
        attrs["exactDetailSellerIdentitySource"] = "isMarketPlaceItem=false"
    elif seller_name or seller_id:
        attrs["exactDetailSellerIdentitySource"] = "exact detail seller field"

    if seller_name:
        exact.seller_name = seller_name
        attrs["seller"] = seller_name
    if seller_id:
        attrs["sellerId"] = seller_id

    if walmart_seller:
        exact.seller_name = exact.seller_name or "Walmart"
        attrs["seller"] = exact.seller_name
        attrs["walmartSeller"] = "yes"
        attrs["exactDetailSellerIdentityStatus"] = "verified"
    elif seller_name or seller_id:
        attrs["walmartSeller"] = "no"
        attrs["exactDetailSellerIdentityStatus"] = "verified"
    else:
        attrs["walmartSeller"] = "no" if marketplace is True else "unknown"
        attrs["exactDetailSellerIdentityStatus"] = "missing"

    exact.variant_attributes = attrs


def _seller_is_walmart(*, seller_name: str | None, seller_id: str | None) -> bool:
    normalized_name = " ".join(str(seller_name or "").strip().lower().split())
    normalized_id = str(seller_id or "").strip().upper()
    return normalized_name in {
        "walmart",
        "walmart.com",
        "walmart stores inc",
        "walmart stores, inc.",
    } or normalized_id in {
        "0",
        "F55CDC31AB754BB68FE0B39041159D63",
        "WALMART",
    }


def _path_value(value: Any, *path: str) -> Any:
    current = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        text = " ".join(str(value).split()).strip()
        if text and text.lower() not in {"none", "null", "unknown"}:
            return text
    return None


def _first_explicit_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "1", "y"}:
            return True
        if text in {"false", "no", "0", "n"}:
            return False
    return None


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

    exact_current = _positive_number(
        getattr(exact, "api_current_price", None)
        or getattr(exact, "current_price", None)
    )
    if getattr(exact, "api_price_path", None):
        merged_attrs["exactDetailCurrentSource"] = str(exact.api_price_path)

    exact_reference = _trusted_reference(exact)
    if exact_reference is not None:
        reference_source = str(
            getattr(exact, "api_reference_path", None)
            or exact_attrs.get("trustedReferenceSource")
            or exact_attrs.get("apiReferencePath")
            or "walmart.exact_detail.reference_price"
        ).strip()
        exact.api_reference_price = exact_reference
        exact.typical_price = exact_reference
        exact.api_reference_path = reference_source
        exact.api_discount_percent = _percent_off(exact_current, exact_reference)
        merged_attrs["exactDetailReferenceSource"] = reference_source
        merged_attrs["exactDetailReferenceStatus"] = "trusted"
        merged_attrs["referencePriceTrusted"] = "yes"
        merged_attrs["trustedReferencePrice"] = f"{exact_reference:.2f}"
        merged_attrs["trustedReferenceSource"] = reference_source
        merged_attrs.pop("observedPriceFallback", None)
    else:
        for key in _PRICE_PROOF_ATTRIBUTE_KEYS:
            merged_attrs.pop(key, None)
        exact.typical_price = None
        exact.api_reference_price = None
        exact.api_reference_path = None
        exact.api_discount_percent = None
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
    return round((reference - current) / reference * 100.0, 2)


def _positive_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
