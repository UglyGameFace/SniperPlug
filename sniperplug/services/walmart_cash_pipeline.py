from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
import asyncio
import inspect
import os

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_cash_offers import walmart_cash_search_terms
from sniperplug.services.walmart_promo_classifier import (
    WalmartPromoScan,
    classify_walmart_api_promos,
    promo_counts_from_scans,
)


@dataclass(frozen=True)
class WalmartApiCapability:
    mode: str
    detail_access: bool
    label: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WalmartDetailResult:
    candidate: SourceCandidate
    detail_checked: bool
    detail_unavailable: bool
    timed_out: bool
    promo_scan: WalmartPromoScan | None
    note: str


@dataclass(frozen=True)
class WalmartCashDiscovery:
    used_queries: tuple[str, ...]
    search_rows_checked: int
    detail_rows_checked: int
    cash_candidates: tuple[SourceCandidate, ...]
    warnings: tuple[str, ...]
    promo_counts: dict[str, int]
    detail_unavailable: bool
    partial: bool
    capability: WalmartApiCapability
    debug_lines: tuple[str, ...]


def detect_walmart_api_capability(provider: Any) -> WalmartApiCapability:
    provider = _unwrap_walmart_provider(provider)
    cfg = getattr(provider, "config", None)
    enabled = bool(getattr(cfg, "enabled", False))
    has_signed = bool(enabled and getattr(cfg, "consumer_id", None) and getattr(cfg, "private_key_b64", None))
    has_oauth = bool(
        os.getenv("WALMART_OAUTH_ACCESS_TOKEN", "").strip()
        or os.getenv("WALMART_SOLUTION_PROVIDER_OAUTH_TOKEN", "").strip()
    )
    detail_method = callable(getattr(provider, "fetch_product_detail_payload", None))

    if has_oauth:
        return WalmartApiCapability(
            mode="oauth_solution_provider",
            detail_access=detail_method,
            label="OAuth/Solution Provider configured",
            notes=("Full promo detail may be available when Walmart grants the app access.",),
        )

    if has_signed:
        return WalmartApiCapability(
            mode="signed_affiliate_api",
            detail_access=detail_method,
            label="Signed Affiliate API configured",
            notes=("Search access is configured. Detail promo access depends on Walmart accepting the detail endpoint.",),
        )

    return WalmartApiCapability(
        mode="search_only_or_disabled",
        detail_access=False,
        label="Search-only/disabled API access",
        notes=("Walmart Cash cannot be proven until authenticated Walmart API access exposes promo detail.",),
    )


async def run_walmart_cash_discovery(
    provider: Any,
    *,
    search: str,
    max_results: int,
    requested_by: str,
) -> WalmartCashDiscovery:
    # Cash Finder needs live Walmart API truth, not cached normalized scan rows.
    # The normal bot registers CachedWalmartProvider for public deal scans, so
    # unwrap it here to avoid DB/cache work blocking the command response and to
    # expose the real provider's signed config/detail method to the probe.
    api_provider = _unwrap_walmart_provider(provider)
    capability = detect_walmart_api_capability(api_provider)
    queries = walmart_cash_search_terms(search)
    per_route_limit = max(3, min(12, int(max_results)))
    scan_jobs = [(query, 1) for query in queries[:2]]
    used_queries = tuple(query for query, _page in scan_jobs)
    warnings: list[str] = []
    all_candidates: list[SourceCandidate] = []

    provider_timeout = int(getattr(getattr(api_provider, "config", None), "timeout_seconds", 12) or 12)
    route_timeout = max(provider_timeout + 6, 18)
    semaphore = asyncio.Semaphore(2)

    async def run_one_route(query: str, page: int):
        async with semaphore:
            try:
                result = await asyncio.wait_for(
                    api_provider.scan(
                        ProviderScanRequest(
                            source_key="walmart",
                            query=query.strip(),
                            max_results=per_route_limit,
                            page=page,
                            metadata={
                                "requested_by": requested_by,
                                "mode": "walmart_cash",
                                "api_truth_only": "yes",
                                "skip_scan_cache": "yes",
                            },
                        )
                    ),
                    timeout=route_timeout,
                )
                return result
            except asyncio.TimeoutError:
                warnings.append(f"Timed out checking `{query}` page {page}; partial result, not a proven no-offer result.")
                return None
            except Exception as exc:
                warnings.append(f"Skipped `{query}` page {page}: {type(exc).__name__}")
                return None

    results = await asyncio.gather(*(run_one_route(query, page) for query, page in scan_jobs))

    for result in results:
        if result is None:
            continue
        all_candidates.extend(result.candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)

    candidates = [_strip_search_level_cash_attrs(candidate) for candidate in _dedupe_candidates(all_candidates)]
    detail_results = await _enrich_candidates_with_detail(api_provider, candidates, capability=capability)

    scans = [result.promo_scan for result in detail_results if result.promo_scan is not None]
    detail_rows_checked = sum(1 for result in detail_results if result.detail_checked)
    detail_unavailable = bool(candidates) and detail_rows_checked == 0
    timed_out = any(result.timed_out for result in detail_results)

    for result in detail_results:
        if result.note and result.note not in warnings:
            warnings.append(result.note)

    cash_candidates = tuple(
        result.candidate
        for result in detail_results
        if result.detail_checked and str(result.candidate.variant_attributes.get("walmartCashApiProof") or "").lower() == "yes"
    )

    partial = timed_out or detail_unavailable or any("partial result" in warning.lower() for warning in warnings)

    return WalmartCashDiscovery(
        used_queries=used_queries,
        search_rows_checked=len(candidates),
        detail_rows_checked=detail_rows_checked,
        cash_candidates=cash_candidates,
        warnings=tuple(warnings[:8]),
        promo_counts=promo_counts_from_scans(scans),
        detail_unavailable=detail_unavailable,
        partial=partial,
        capability=capability,
        debug_lines=tuple(_debug_line(result) for result in detail_results[:8]),
    )


async def run_walmart_api_probe(
    provider: Any,
    *,
    query: str,
    max_results: int,
    requested_by: str,
) -> WalmartCashDiscovery:
    # Probe uses the same real pipeline so diagnostics cannot lie.
    return await run_walmart_cash_discovery(
        provider,
        search=query,
        max_results=max(1, min(8, int(max_results))),
        requested_by=requested_by,
    )


async def _enrich_candidates_with_detail(
    provider: Any,
    candidates: list[SourceCandidate],
    *,
    capability: WalmartApiCapability,
) -> list[WalmartDetailResult]:
    provider = _unwrap_walmart_provider(provider)
    if not candidates:
        return []

    detail_method = getattr(provider, "fetch_product_detail_payload", None)
    if not callable(detail_method) or not capability.detail_access:
        return [
            WalmartDetailResult(
                candidate=candidate,
                detail_checked=False,
                detail_unavailable=True,
                timed_out=False,
                promo_scan=None,
                note="Walmart did not expose full promo detail through the current API access.",
            )
            for candidate in candidates[:12]
        ]

    semaphore = asyncio.Semaphore(3)

    async def enrich(candidate: SourceCandidate) -> WalmartDetailResult:
        async with semaphore:
            product_id = str(candidate.product_id or candidate.sku or "").strip()
            if not product_id:
                return WalmartDetailResult(candidate, False, True, False, None, "Skipped one product with no Walmart product ID for detail proof.")

            try:
                payload = await asyncio.wait_for(_call_detail_method(detail_method, product_id), timeout=8)
            except asyncio.TimeoutError:
                return WalmartDetailResult(candidate, False, True, True, None, f"Timed out checking detail promo proof for `{product_id}`.")
            except Exception as exc:
                return WalmartDetailResult(candidate, False, True, False, None, f"Detail promo proof unavailable for `{product_id}`: {type(exc).__name__}")

            item = _extract_detail_item(payload)
            if not isinstance(item, dict):
                return WalmartDetailResult(candidate, False, True, False, None, f"Detail endpoint returned no usable product promo row for `{product_id}`.")

            scan = classify_walmart_api_promos(item, current_price=candidate.current_price)
            enriched = replace(candidate, variant_attributes=dict(candidate.variant_attributes), signals=list(candidate.signals))
            enriched.variant_attributes.update(scan.as_attributes())

            if scan.cash is not None:
                enriched.variant_attributes.update(scan.cash.as_attributes())
                if scan.cash.signal() not in enriched.signals:
                    enriched.signals.append(scan.cash.signal())

            return WalmartDetailResult(enriched, True, False, False, scan, "")

    return list(await asyncio.gather(*(enrich(candidate) for candidate in candidates[:12])))


async def _call_detail_method(detail_method: Any, product_id: str) -> Any:
    result = detail_method(product_id)
    if inspect.isawaitable(result):
        return await result
    return result


def _unwrap_walmart_provider(provider: Any) -> Any:
    seen: set[int] = set()
    current = provider
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        inner = getattr(current, "inner", None)
        if inner is None or inner is current:
            break
        current = inner
    return current if current is not None else provider


def _strip_search_level_cash_attrs(candidate: SourceCandidate) -> SourceCandidate:
    cloned = replace(candidate, variant_attributes=dict(candidate.variant_attributes), signals=list(candidate.signals))
    for key in list(cloned.variant_attributes.keys()):
        if key.lower().startswith("walmartcash"):
            cloned.variant_attributes.pop(key, None)
    cloned.signals = [signal for signal in cloned.signals if "walmart cash" not in str(signal).lower()]
    return cloned


def _dedupe_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    output: list[SourceCandidate] = []

    for candidate in candidates:
        key = str(candidate.product_id or candidate.sku or candidate.product_url or candidate.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(candidate)

    return output


def _extract_detail_item(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if payload.get("itemId") or payload.get("usItemId") or payload.get("name"):
            return payload

        for key in ("item", "product", "data"):
            child = payload.get(key)
            if isinstance(child, dict):
                found = _extract_detail_item(child)
                if found:
                    return found

        for key in ("items", "products", "itemResponse", "results"):
            child = payload.get(key)
            if isinstance(child, list):
                for item in child:
                    found = _extract_detail_item(item)
                    if found:
                        return found

    return None


def _debug_line(result: WalmartDetailResult) -> str:
    candidate = result.candidate
    title = " ".join(str(candidate.title or "Untitled").split())[:70]

    if not result.detail_checked:
        return f"{title}: detail proof not checked — {result.note}"

    scan = result.promo_scan
    if scan is None:
        return f"{title}: detail checked, no promo paths exposed"

    bits: list[str] = []
    if scan.cash:
        bits.append(f"Walmart Cash ${scan.cash.amount:,.2f} at {scan.cash.proof_path}")
    if scan.cart_promo:
        bits.append(f"Cart Promo at {scan.cart_promo.proof_path}")
    if scan.onepay:
        bits.append(f"OnePay at {scan.onepay.proof_path}")
    if scan.markdown:
        bits.append(f"Markdown at {scan.markdown.proof_path}")
    if scan.clearance:
        bits.append(f"Clearance at {scan.clearance.proof_path}")
    if scan.generic:
        bits.append(f"Generic promo at {scan.generic.proof_path}")

    return f"{title}: " + ("; ".join(bits) if bits else "detail checked, no promo proof")
