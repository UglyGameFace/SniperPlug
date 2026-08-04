from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeVar

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderScanResult
from sniperplug.providers.registry import provider_registry
from sniperplug.services.walmart_exact_price_enrichment import (
    ExactPriceEnrichmentResult,
    enrich_walmart_exact_prices,
)


_ResultT = TypeVar("_ResultT")


async def run_walmart_autoscan_scan_off_event_loop(
    query: str,
    page: int,
    max_results: int,
    sort_value: str | None,
    order_value: str | None,
    requested_by: str,
) -> ProviderScanResult:
    """Run Walmart HTTP and candidate parsing outside Discord's event loop.

    The registered cached provider is retained so its hard-failure diagnostics
    remain identical. ``autoscan_lightweight`` guarantees that this worker-loop
    call does not use Turso-backed per-route cache or scan-history operations,
    whose asyncio locks belong to the main bot loop.
    """

    provider = provider_registry.get("walmart")
    if provider is None:
        return ProviderScanResult(
            provider_key="walmart",
            candidates=(),
            warnings=("Walmart provider is not registered.",),
        )

    request = ProviderScanRequest(
        source_key="walmart",
        query=str(query or "").strip(),
        max_results=max(1, int(max_results)),
        page=max(1, int(page)),
        sort=sort_value,
        order=order_value,
        metadata={
            "requested_by": str(requested_by or "global_catalog_autoscan"),
            "autoscan_lightweight": "yes",
            "off_event_loop": "yes",
        },
    )
    result = await _run_coroutine_factory_off_event_loop(
        lambda: provider.scan(request)
    )
    if isinstance(result, ProviderScanResult):
        return result
    return ProviderScanResult(
        provider_key="walmart",
        candidates=(),
        warnings=(
            "Walmart autoscan worker returned an invalid provider result: "
            f"{type(result).__name__}",
        ),
    )


async def enrich_walmart_exact_prices_off_event_loop(
    candidates: Iterable[SourceCandidate],
    *,
    provider: Any,
    limit: int,
    concurrency: int,
    timeout_seconds: float,
    min_discount: int,
) -> ExactPriceEnrichmentResult:
    """Run foreground exact-detail parsing and proof merging off-loop."""

    candidate_snapshot = list(candidates)
    return await _run_coroutine_factory_off_event_loop(
        lambda: enrich_walmart_exact_prices(
            candidate_snapshot,
            provider=provider,
            limit=limit,
            concurrency=concurrency,
            timeout_seconds=timeout_seconds,
            min_discount=min_discount,
        )
    )


async def _run_coroutine_factory_off_event_loop(
    factory: Callable[[], Awaitable[_ResultT]],
) -> _ResultT:
    return await asyncio.to_thread(_run_coroutine_factory, factory)


def _run_coroutine_factory(
    factory: Callable[[], Awaitable[_ResultT]],
) -> _ResultT:
    return asyncio.run(factory())
