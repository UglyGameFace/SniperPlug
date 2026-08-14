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
from sniperplug.services.walmart_global_catalog_autoscan import (
    load_global_catalog_state,
)


CATALOG_SEARCH_CONCURRENCY = 3
CATALOG_PAGES_PER_QUERY = 2
CATALOG_PROVIDER_RESULT_LIMIT = 1000
CATALOG_MAX_PAGE = max(1, CATALOG_PROVIDER_RESULT_LIMIT // hunt.RESULTS_PER_PAGE)
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


def catalog_pages_for_completed_passes(completed_passes: int) -> tuple[int, ...]:
    """Return the next persistent two-page Affiliate search window.

    Walmart's Affiliate search endpoint exposes at most the first 1,000 results
    for a keyword and SniperPlug requests 25 rows per page. The old catalog loop
    repeated pages 1-2 on every full route pass, so dozens of completed passes
    did not deepen coverage. Use the durable completed-pass counter as the page
    cursor: every route in one full pass sees the same page window, then the next
    full pass advances by two pages and wraps after page 40.
    """

    window_count = max(1, (CATALOG_MAX_PAGE + CATALOG_PAGES_PER_QUERY - 1) // CATALOG_PAGES_PER_QUERY)
    window_index = max(0, int(completed_passes)) % window_count
    start = (window_index * CATALOG_PAGES_PER_QUERY) + 1
    end = min(CATALOG_MAX_PAGE, start + CATALOG_PAGES_PER_QUERY - 1)
    return tuple(range(start, end + 1))


async def _current_catalog_pages(db) -> tuple[tuple[int, ...], int]:
    completed_passes = 0
    if db is not None:
        try:
            state = await load_global_catalog_state(db)
            completed_passes = max(0, int(state.completed_passes))
        except Exception:
            # Discovery must remain available if diagnostics/state read fails.
            # Falling back to the shallow window is safe and does not advance any
            # durable cursor incorrectly; the global claim itself still owns route
            # progression and completion.
            completed_passes = 0
    return catalog_pages_for_completed_passes(completed_passes), completed_passes


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

    Search depth rotates with the durable global completed-pass counter instead
    of repeating pages 1-2 forever. With two pages per route pass and Walmart's
    1,000-result search bound, twenty completed full route passes cover pages
    1-40 for every configured query before wrapping. This deepens discovery
    without increasing requests per minute or creating one scan per Discord
    server.

    Keeping exact verification out of the catalog pass prevents the catalog
    worker from holding the shared Walmart operation lock through search pages
    plus detail requests. That gives the exact worker a fair turn and prevents
    discovery from outrunning verification indefinitely.
    """

    semaphore = asyncio.Semaphore(CATALOG_SEARCH_CONCURRENCY)
    all_candidates = []
    warnings: list[str] = []
    pages_checked = 0
    searches_attempted = 0
    preset_queries = tuple(hunt.dedupe_strings(preset.queries))
    catalog_pages, completed_passes = await _current_catalog_pages(db)

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
        for page in catalog_pages
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

    page_label = (
        str(catalog_pages[0])
        if len(catalog_pages) == 1
        else f"{catalog_pages[0]}-{catalog_pages[-1]}"
    )
    warnings.append(
        "Walmart catalog page window: "
        f"pages **{page_label}**/**{CATALOG_MAX_PAGE}** • "
        f"completed full passes before this batch **{completed_passes}** • "
        f"requests per route unchanged at **{len(catalog_pages)}**."
    )
    warnings.append(
        "Walmart catalog discovery-only pass: "
        f"page window **{page_label}**/**{CATALOG_MAX_PAGE}** • "
        f"completed full passes before this batch **{completed_passes}** • "
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
