from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import DealProvider, ProviderHealth, ProviderScanRequest, ProviderScanResult, ProviderStatus
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
_HARD_FAILURE_TERMS = (
    "walmart api http",
    "walmart api network error",
    "walmart private key",
    "missing walmart config",
    "walmart api returned non-json",
    "walmart api returned unexpected payload shape",
    "disabled: set walmart_provider_enabled",
)
_LIGHTWEIGHT_SCAN_NOTE = "Autoscan lightweight scan: skipped per-route Turso cache, scan-run, identity, and price-observation writes."


class CachedWalmartProvider(DealProvider):
    """Walmart provider with Turso-backed scan cache, identity memory, and price observations."""

    provider_key = "walmart"
    display_name = "Walmart"
    capabilities = WalmartProvider.capabilities

    def __init__(self, db: Any, inner: WalmartProvider | None = None) -> None:
        self.db = db
        self.inner = inner or WalmartProvider(configured=False)

    @property
    def config(self):
        return getattr(self.inner, "config", None)

    async def healthcheck(self) -> ProviderHealth:
        health = await self.inner.healthcheck()
        if not health.ok:
            return health

        credential_error = walmart_credential_validation_error(self.inner)
        if credential_error:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.ERROR,
                message=credential_error,
            )
        return health

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        return await self.inner.fetch_product_detail_payload(item_id)

    async def _scan_inner_direct(self, request: ProviderScanRequest) -> ProviderScanResult:
        return await self.inner.scan(request)

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        if str(request.metadata.get("skip_scan_cache") or "").lower() in {"1", "true", "yes", "on"}:
            return mark_hard_provider_failure(await self._scan_inner_direct(request))

        if lightweight_autoscan_request(request):
            result = mark_hard_provider_failure(await self._scan_inner_direct(request))
            warnings = tuple(dict.fromkeys((*result.warnings, _LIGHTWEIGHT_SCAN_NOTE)))
            return _copy_result(
                result,
                warnings=warnings,
                metadata={
                    **result.metadata,
                    "cache_hit": False,
                    "autoscan_lightweight": True,
                    "db_persistence_skipped": True,
                },
            )

        scan_id = None
        requested_by = _int_or_none(request.metadata.get("requested_by"))
        guild_id = _int_or_none(request.metadata.get("guild_id"))
        errors: list[str] = []
        try:
            scan_id = await self.db.start_scan_run(
                guild_id=guild_id,
                user_id=requested_by,
                retailer=self.provider_key,
                query=request.query,
            )
        except Exception as exc:
            errors.append(f"walmart scan-run start failed: {clean_warning_text(exc)}")

        cache_key = _scan_cache_key(request)
        provider_calls = 0
        cache_hits = 0
        cache_misses = 0
        cached_result_used = False

        try:
            cached = await self.db.get_scan_result_cache(cache_key)
        except Exception as exc:
            cached = None
            errors.append(f"walmart scan cache read failed: {clean_warning_text(exc)}")

        if cached:
            cached_result = mark_hard_provider_failure(_result_from_cache(cached["results"]))
            if provider_scan_had_hard_failure(cached_result):
                errors.append("ignored cached Walmart provider failure result; forcing a live retry")
                cached = None
            else:
                cache_hits = 1
                cached_result_used = True
                warnings = list(cached_result.warnings)
                warnings.append("Walmart scan cache hit: reused recent normalized scan result.")
                result = _copy_result(
                    cached_result,
                    warnings=tuple(warnings),
                    metadata={**cached_result.metadata, "cache_hit": True, "cache_key": cache_key},
                )

        if not cached_result_used:
            cache_misses = 1
            provider_calls = 1
            result = mark_hard_provider_failure(await self._scan_inner_direct(request))
            if provider_scan_had_hard_failure(result):
                errors.append(provider_failure_summary(result) or "Walmart provider hard failure")
            else:
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
                    errors.append(f"walmart scan cache write failed: {clean_warning_text(exc)}")
            result = _copy_result(result, metadata={**result.metadata, "cache_hit": False, "cache_key": cache_key})

        errors.extend(await self._persist_candidates(result.candidates))
        errors.extend(await self._record_query_memory(request, result))

        if scan_id:
            try:
                await self.db.finish_scan_run(
                    scan_id,
                    status="provider_error" if provider_scan_had_hard_failure(result) else "finished",
                    provider_calls=provider_calls,
                    cache_hits=cache_hits,
                    cache_misses=cache_misses,
                    results_found=len(result.candidates),
                    errors=errors,
                )
            except Exception as exc:
                errors.append(f"walmart scan-run finish failed: {clean_warning_text(exc)}")

        if errors:
            unique_errors = tuple(dict.fromkeys(errors))
            sample = "; ".join(unique_errors[:2])
            summary = f"Walmart persistence degraded: {len(unique_errors)} write error(s)"
            if sample:
                summary += f" (sample: {sample})"
            warnings = tuple(dict.fromkeys((*result.warnings, clean_warning_text(summary))))
            result = _copy_result(
                result,
                warnings=warnings,
                metadata={
                    **result.metadata,
                    "persistence_degraded": True,
                    "persistence_error_count": len(unique_errors),
                    "persistence_errors": unique_errors[:12],
                },
            )
        return result

    async def _persist_candidates(self, candidates: tuple[SourceCandidate, ...]) -> list[str]:
        errors: list[str] = []
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
            except Exception as exc:
                errors.append(f"identity write failed for {product_key}: {clean_warning_text(exc)}")

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
                        reference_source=(candidate.variant_attributes or {}).get("trustedReferenceSource")
                        or (candidate.variant_attributes or {}).get("reference_price_source"),
                        source_key=candidate.source_key,
                    )
                except Exception as exc:
                    errors.append(f"price observation failed for {product_key}: {clean_warning_text(exc)}")
        return errors

    async def _record_query_memory(self, request: ProviderScanRequest, result: ProviderScanResult) -> list[str]:
        guild_id = _int_or_none(request.metadata.get("guild_id")) or 0
        if guild_id <= 0 or not request.query or provider_scan_had_hard_failure(result):
            return []

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
        except Exception as exc:
            return [f"query performance write failed: {clean_warning_text(exc)}"]
        return []


def walmart_credential_validation_error(provider: WalmartProvider) -> str | None:
    """Return an operator-visible credential error before scans pretend to be empty."""

    loader = getattr(provider, "_load_private_key", None)
    if not callable(loader):
        return None
    try:
        loader()
    except Exception as exc:
        return f"Walmart credentials are present but unusable: {clean_warning_text(exc)}"
    return None


def lightweight_autoscan_request(request: ProviderScanRequest) -> bool:
    if truthy(request.metadata.get("autoscan_lightweight")):
        return True
    return str(request.metadata.get("requested_by") or "").strip().lower() == "autoscan"


def provider_scan_had_hard_failure(result: ProviderScanResult) -> bool:
    if result.candidates:
        return False
    metadata = dict(result.metadata or {})
    if truthy(metadata.get("provider_hard_failure")):
        return True
    return bool(provider_failure_summary(result))


def provider_failure_summary(result: ProviderScanResult) -> str | None:
    for warning in result.warnings or ():
        text = clean_warning_text(warning)
        lowered = text.lower()
        if any(term in lowered for term in _HARD_FAILURE_TERMS):
            return text
    return None


def mark_hard_provider_failure(result: ProviderScanResult) -> ProviderScanResult:
    summary = provider_failure_summary(result)
    if not summary:
        return result
    warnings = tuple(dict.fromkeys((*result.warnings, f"Walmart provider hard failure: {summary}")))
    return _copy_result(
        result,
        warnings=warnings,
        metadata={**result.metadata, "provider_hard_failure": True, "provider_failure_summary": summary},
    )


def clean_warning_text(value: Any, *, limit: int = 280) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _copy_result(
    result: ProviderScanResult,
    *,
    warnings: tuple[str, ...] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProviderScanResult:
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
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
