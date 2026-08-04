from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services import verified_discount_hunt as hunt
from sniperplug.services.deal_finder_telemetry import tag_candidates_with_route
from sniperplug.services.walmart_autoscan_offloop import (
    run_walmart_autoscan_scan_off_event_loop,
)
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
)


CATALOG_SEARCH_CONCURRENCY = 3
CATALOG_PAGES_PER_QUERY = 2
CATALOG_SORT_PASSES: tuple[tuple[str | None, str | None], ...] = ((None, None),)


@dataclass(frozen=True)
class WalmartCatalogDiscoveryResult:
    pages_checked: int = 0
    products_checked: int = 0
    searches_attempted: int = 0
    unique_candidates: int = 0
    candidates_with_item_id: int = 0
    warnings: tuple[str, ...] = ()


def _walmart_item_id(candidate) -> str:
    for value in (
        getattr(candidate, "product_id", None),
        getattr(candidate, "sku", None),
    ):
        text = str(value or "").strip()
        if text.isdigit():
            return text
    return ""


async def discover_walmart_catalog_candidates(
    *,
    requested_by: str,
    preset: HuntPreset,
    db,
    min_discount: int,
) -> WalmartCatalogDiscoveryResult:
    """Discover Walmart item IDs without doing foreground detail verification.

    Catalog search is candidate discovery only. Every candidate with a usable
    Walmart item ID is written to the durable exact-detail queue in one bulk
    operation. The dedicated queue worker is the sole owner of exact item,
    selected offer, seller, variant, fulfillment, current-price, and trusted
    reference-price verification.

    Keeping exact verification out of the catalog pass prevents the catalog
    worker from holding the shared Walmart operation lock through eight search
    pages plus another 24 detail requests. That gives the exact worker a fair
    turn and prevents discovery from outrunning verification indefinitely.
    """

    semaphore = asyncio.Semaphore(CATALOG_SEARCH_CONCURRENCY)
    all_candidates = []
    warnings: list[str] = []
    pages_checked = 0
    searches_attempted = 0
    preset_queries = tuple(hunt.dedupe_strings(preset.queries))

    async def scan_one(
        query: str,
        page: int,
        sort_value: str | None,
        order_value: str | None,
    ) -> tuple[str, ProviderScanResult]:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            await asyncio.sleep(0)
            return query, await run_walmart_autoscan_scan_off_event_loop(
                query,
                page,
                hunt.RESULTS_PER_PAGE,
                sort_value,
                order_value,
                requested_by,
            )

    tasks = [
        scan_one(query, page, sort_value, order_value)
        for query in preset_queries
        for sort_value, order_value in CATALOG_SORT_PASSES
        for page in range(1, CATALOG_PAGES_PER_QUERY + 1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in results:
        pages_checked += 1
        if isinstance(item, BaseException) or not isinstance(item, tuple) or len(item) != 2:
            warning = (
                str(item) or item.__class__.__name__
                if isinstance(item, BaseException)
                else f"bad Walmart route result: {type(item).__name__}"
            )
            if warning not in warnings:
                warnings.append(warning)
            continue

        query, result = item
        if not isinstance(result, ProviderScanResult):
            warning = f"bad Walmart provider result for {query}: {type(result).__name__}"
            if warning not in warnings:
                warnings.append(warning)
            continue

        candidates = list(result.candidates)
        tag_candidates_with_route(candidates, query=query)
        all_candidates.extend(candidates)
        for warning in result.warnings:
            if warning not in warnings:
                warnings.append(warning)

    deduped = deal_scanner.dedupe_candidates(all_candidates)
    item_id_count = sum(1 for candidate in deduped if _walmart_item_id(candidate))

    if db is not None and deduped:
        try:
            queue_result = await enqueue_walmart_exact_verification_candidates_bulk(
                db,
                deduped,
                min_discount=min_discount,
                source_label=f"{requested_by}:{preset.key}",
            )
            warnings.append(queue_result.summary_line())
        except Exception as error:  # noqa: BLE001 - later passes can retry discovery.
            warnings.append(
                "Walmart exact-detail queue write failed safely: "
                f"{type(error).__name__}: {error}"
            )

    warnings.append(
        "Walmart catalog discovery-only pass: "
        f"returned **{len(all_candidates)}** • unique **{len(deduped)}** • "
        f"usable item IDs **{item_id_count}** • foreground exact checks **0**."
    )
    return WalmartCatalogDiscoveryResult(
        pages_checked=pages_checked,
        products_checked=len(all_candidates),
        searches_attempted=searches_attempted,
        unique_candidates=len(deduped),
        candidates_with_item_id=item_id_count,
        warnings=tuple(warnings),
    )
