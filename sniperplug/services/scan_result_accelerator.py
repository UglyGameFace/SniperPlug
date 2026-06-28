from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult


DEFAULT_SCAN_CACHE_MINUTES = 10
_SOURCE_CANDIDATE_FIELDS = {field.name for field in fields(SourceCandidate)}
_HARD_FAILURE_TERMS = (
    "walmart api http",
    "walmart api network error",
    "walmart private key",
    "missing walmart config",
    "provider hard failure",
    "walmart api returned non-json",
    "walmart api returned unexpected payload shape",
    "disabled: set walmart_provider_enabled",
)


@dataclass(frozen=True)
class ScanCacheOutcome:
    result: ProviderScanResult
    cache_hit: bool
    cache_key: str


async def cached_provider_scan_or_run(
    db,
    *,
    retailer: str,
    query: str,
    page: int,
    max_results: int,
    sort_value: str | None,
    order_value: str | None,
    force_refresh: bool,
    runner: Callable[[], Awaitable[ProviderScanResult]],
    ttl_minutes: int = DEFAULT_SCAN_CACHE_MINUTES,
) -> ScanCacheOutcome:
    """Use DB-backed route cache for exact provider scan requests.

    This cache is intentionally short-lived. It speeds up repeated `/deals` runs
    and button refreshes without letting stale glitches hide for hours.
    Provider/auth failures are never trusted as cache hits and are never written
    back to cache, because that turns a real outage into fake empty scans.
    """
    key = scan_cache_key(
        retailer=retailer,
        query=query,
        page=page,
        max_results=max_results,
        sort_value=sort_value,
        order_value=order_value,
    )
    if db is not None and not force_refresh:
        cached = await safe_get_scan_cache(db, key)
        if cached:
            result = mark_hard_provider_failure(deserialize_provider_scan_result(cached["results"]))
            if not provider_scan_had_hard_failure(result):
                result.metadata["scan_cache"] = "hit"
                result.metadata["scan_cache_key"] = key
                return ScanCacheOutcome(result=result, cache_hit=True, cache_key=key)

    result = mark_hard_provider_failure(await runner())
    result.metadata["scan_cache"] = "provider_error" if provider_scan_had_hard_failure(result) else ("miss" if db is not None else "disabled")
    result.metadata["scan_cache_key"] = key
    if db is not None and not provider_scan_had_hard_failure(result):
        await safe_set_scan_cache(
            db,
            key,
            retailer=retailer,
            query=query,
            result=result,
            ttl_minutes=ttl_minutes,
            request={
                "query": query,
                "page": page,
                "max_results": max_results,
                "sort": sort_value,
                "order": order_value,
            },
        )
    return ScanCacheOutcome(result=result, cache_hit=False, cache_key=key)


def scan_cache_key(*, retailer: str, query: str, page: int, max_results: int, sort_value: str | None, order_value: str | None) -> str:
    payload = json.dumps(
        {
            "retailer": retailer.strip().lower(),
            "query": " ".join((query or "").strip().lower().split()),
            "page": int(page),
            "max_results": int(max_results),
            "sort": sort_value or "",
            "order": order_value or "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"scan:{retailer.strip().lower()}:{digest}"


async def safe_get_scan_cache(db, key: str) -> dict | None:
    try:
        return await db.get_scan_result_cache(key)
    except Exception:
        return None


async def safe_set_scan_cache(db, key: str, *, retailer: str, query: str, result: ProviderScanResult, ttl_minutes: int, request: dict) -> None:
    try:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=max(1, int(ttl_minutes)))).isoformat()
        await db.set_scan_result_cache(
            key,
            retailer=retailer,
            query=query,
            request=request,
            results=serialize_provider_scan_result(result),
            total_results=int(result.total_results or len(result.candidates) or 0),
            expires_at=expires_at,
        )
    except Exception:
        return


def serialize_provider_scan_result(result: ProviderScanResult) -> dict:
    return {
        "provider_key": result.provider_key,
        "candidates": [asdict(candidate) for candidate in result.candidates],
        "warnings": list(result.warnings),
        "total_results": result.total_results,
        "page": result.page,
        "page_size": result.page_size,
        "start_index": result.start_index,
        "has_next_page": result.has_next_page,
        "metadata": dict(result.metadata or {}),
    }


def deserialize_provider_scan_result(payload: dict) -> ProviderScanResult:
    candidates = []
    for raw in payload.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        clean = {key: value for key, value in raw.items() if key in _SOURCE_CANDIDATE_FIELDS}
        candidates.append(SourceCandidate(**clean))
    return ProviderScanResult(
        provider_key=str(payload.get("provider_key") or "walmart"),
        candidates=tuple(candidates),
        warnings=tuple(payload.get("warnings") or ()),
        total_results=payload.get("total_results"),
        page=int(payload.get("page") or 1),
        page_size=payload.get("page_size"),
        start_index=payload.get("start_index"),
        has_next_page=bool(payload.get("has_next_page")),
        metadata=dict(payload.get("metadata") or {}),
    )


def provider_scan_had_hard_failure(result: ProviderScanResult) -> bool:
    if result.candidates:
        return False
    if str((result.metadata or {}).get("provider_hard_failure") or "").lower() in {"1", "true", "yes", "on"}:
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
    warnings = tuple(dict.fromkeys((*result.warnings, f"Provider hard failure: {summary}")))
    return ProviderScanResult(
        provider_key=result.provider_key,
        candidates=result.candidates,
        warnings=warnings,
        total_results=result.total_results,
        page=result.page,
        page_size=result.page_size,
        start_index=result.start_index,
        has_next_page=result.has_next_page,
        metadata={**result.metadata, "provider_hard_failure": True, "provider_failure_summary": summary},
    )


def clean_warning_text(value: Any, *, limit: int = 280) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")
