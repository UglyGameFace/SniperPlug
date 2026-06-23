from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import DealProvider, ProviderHealth, ProviderScanRequest, ProviderScanResult
from sniperplug.providers.walmart import WalmartProvider


WALMART_SCAN_CACHE_MINUTES = 10
_SOURCE_CANDIDATE_FIELDS = {field.name for field in fields(SourceCandidate)}
_CACHE_SCOPE_METADATA_KEYS = (
    "zip_code",
    "postal_code",
    "store_id",
    "store_ids",
    "walmart_store_id",
    "location",
    "fulfillment",
)


class CachedWalmartProvider(DealProvider):
    """Walmart provider with Turso-backed scan cache, identity memory, and price observations."""

    provider_key = "walmart"
    display_name = "Walmart"
    capabilities = WalmartProvider.capabilities

    def __init__(self, db: Any, inner: WalmartProvider | None = None) -> None:
        self.db = db
        self.inner = inner or WalmartProvider(configured=False)

    async def healthcheck(self) -> ProviderHealth:
        return await self.inner.healthcheck()

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        scan_id = None
        requested_by = _int_or_none(request.metadata.get("requested_by"))
        guild_id = _int_or_none(request.metadata.get("guild_id"))
        try:
            scan_id = await self.db.start_scan_run(guild_id=guild_id, user_id=requested_by, retailer=self.provider_key, query=request.query)
        except Exception:
            scan_id = None

        cache_key = _scan_cache_key(request)
        provider_calls = 0
        cache_hits = 0
        cache_misses = 0
        errors: list[str] = []

        try:
            cached = await self.db.get_scan_result_cache(cache_key)
        except Exception as exc:
            cached = None
            errors.append(f"walmart scan cache read failed: {exc}")

        if cached:
            result = _result_from_cache(cached["results"])
            cache_hits = 1
            warnings = list(result.warnings)
            warnings.append("Walmart scan cache hit: reused recent normalized scan result.")
            result = _copy_result(result, warnings=tuple(warnings), metadata={**result.metadata, "cache_hit": True, "cache_key": cache_key})
        else:
            cache_misses = 1
            provider_calls = 1
            result = await self.inner.scan(request)
            try:
                await self.db.set_scan_result_cache(
                    cache_key,
                    retailer=self.provider_key,
                    query=request.query,
                    request=_request_cache_payload(request),
                    results=_result_cache_payload(result),
                    total_results=result.total_results or len(result.candidates),
                    expires_at=_minutes_from_now_iso(WALMART_SCAN_CACHE_MINUTES),
                )
            except Exception as exc:
                errors.append(f"walmart scan cache write failed: {exc}")
            result = _copy_result(result, metadata={**result.metadata, "cache_hit": False, "cache_key": cache_key})

        await self._persist_candidates(result.candidates)
        await self._record_query_memory(request, result)
        if scan_id:
            try:
                await self.db.finish_scan_run(
                    scan_id,
                    status="finished",
                    provider_calls=provider_calls,
                    cache_hits=cache_hits,
                    cache_misses=cache_misses,
                    results_found=len(result.candidates),
                    errors=errors,
                )
            except Exception:
                pass
        return result

    async def _persist_candidates(self, candidates: tuple[SourceCandidate, ...]) -> None:
        for candidate in candidates:
            product_key = _product_key(candidate)
            if not product_key:
                continue
            try:
                await self.db.upsert_product_identity(
                    retailer="Walmart",
                    product_key=product_key,
                    product_id=candidate.product_id,
                    sku=candidate.sku,
                    upc=candidate.upc,
                    model=candidate.model,
                    title=candidate.title,
                    brand=(candidate.variant_attributes or {}).get("brand"),
                    canonical_url=candidate.product_url,
                    image_url=candidate.image_url,
                    last_seen_price=candidate.current_price,
                )
            except Exception:
                pass
            if candidate.current_price is not None and candidate.current_price > 0:
                try:
                    await self.db.record_price_observation(
                        retailer="Walmart",
                        product_key=product_key,
                        current_price=float(candidate.current_price),
                        product_id=candidate.product_id,
                        sku=candidate.sku,
                        upc=candidate.upc,
                        title=candidate.title,
                        product_url=candidate.product_url,
                        reference_price=candidate.typical_price,
                        reference_source=(candidate.variant_attributes or {}).get("trustedReferenceSource") or (candidate.variant_attributes or {}).get("reference_price_source"),
                        source_key=candidate.source_key,
                    )
                except Exception:
                    pass

    async def _record_query_memory(self, request: ProviderScanRequest, result: ProviderScanResult) -> None:
        guild_id = _int_or_none(request.metadata.get("guild_id")) or 0
        if guild_id <= 0 or not request.query:
            return
        discounts: list[float] = []
        verified_hits = 0
        review_hits = 0
        blocked_hits = 0
        for candidate in result.candidates:
            if candidate.typical_price and candidate.current_price and candidate.typical_price > candidate.current_price:
                discount = (candidate.typical_price - candidate.current_price) / candidate.typical_price * 100
                discounts.append(discount)
                if discount >= 50:
                    verified_hits += 1
                elif discount >= 20:
                    review_hits += 1
            else:
                blocked_hits += 1
        avg_discount = sum(discounts) / len(discounts) if discounts else 0.0
        try:
            await self.db.record_query_performance(
                guild_id=guild_id,
                retailer="Walmart",
                query=request.query,
                returned_products=len(result.candidates),
                verified_hits=verified_hits,
                review_hits=review_hits,
                blocked_hits=blocked_hits,
                avg_discount=avg_discount,
            )
        except Exception:
            pass


def _copy_result(result: ProviderScanResult, *, warnings: tuple[str, ...] | None = None, metadata: dict[str, Any] | None = None) -> ProviderScanResult:
    return ProviderScanResult(
        provider_key=result.provider_key,
        candidates=result.candidates,
        warnings=result.warnings if warnings is None else warnings,
        total_results=result.total_results,
        page=result.page,
        page_size=result.page_size,
        start_index=result.start_index,
        has_next_page=result.has_next_page,
        metadata=result.metadata if metadata is None else metadata,
    )


def _scan_cache_key(request: ProviderScanRequest) -> str:
    raw = json.dumps(_request_cache_payload(request), sort_keys=True, separators=(",", ":"))
    return "walmart_scan:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _request_cache_payload(request: ProviderScanRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": (request.query or "").strip().lower(),
        "product_ids": tuple(str(value).strip() for value in request.product_ids),
        "page": max(1, request.page),
        "max_results": max(1, min(request.max_results, 25)),
        "sort": request.sort or "relevance",
        "order": request.order or "",
    }
    scoped_metadata = _cache_scope_metadata(request.metadata)
    if scoped_metadata:
        payload["scope"] = scoped_metadata
    return payload


def _cache_scope_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    scope: dict[str, str] = {}
    for key in _CACHE_SCOPE_METADATA_KEYS:
        value = metadata.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple, set)):
            cleaned = tuple(str(item).strip() for item in value if str(item).strip())
            if cleaned:
                scope[key] = ",".join(cleaned)
            continue
        text = str(value).strip()
        if text:
            scope[key] = text.lower()
    return scope


def _result_cache_payload(result: ProviderScanResult) -> dict[str, Any]:
    return {
        "provider_key": result.provider_key,
        "candidates": [asdict(candidate) for candidate in result.candidates],
        "warnings": list(result.warnings),
        "total_results": result.total_results,
        "page": result.page,
        "page_size": result.page_size,
        "start_index": result.start_index,
        "has_next_page": result.has_next_page,
        "metadata": {k: v for k, v in result.metadata.items() if _jsonable(v)},
    }


def _result_from_cache(data: dict[str, Any]) -> ProviderScanResult:
    candidates = []
    for raw in data.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        clean = {key: value for key, value in raw.items() if key in _SOURCE_CANDIDATE_FIELDS}
        candidates.append(SourceCandidate(**clean))
    return ProviderScanResult(
        provider_key=data.get("provider_key") or "walmart",
        candidates=tuple(candidates),
        warnings=tuple(data.get("warnings") or ()),
        total_results=data.get("total_results"),
        page=int(data.get("page") or 1),
        page_size=data.get("page_size"),
        start_index=data.get("start_index"),
        has_next_page=bool(data.get("has_next_page")),
        metadata=dict(data.get("metadata") or {}),
    )


def _product_key(candidate: SourceCandidate) -> str | None:
    value = candidate.product_id or candidate.selected_offer_id or candidate.upc or candidate.sku
    if not value:
        return None
    return "".join(ch for ch in str(value).lower() if ch.isalnum()) or None


def _minutes_from_now_iso(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _jsonable(value: Any) -> bool:
    try:
        json.dumps(value)
        return True
    except Exception:
        return False


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None
