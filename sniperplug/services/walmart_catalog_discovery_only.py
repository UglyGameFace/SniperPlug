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
    GlobalCatalogClaim,
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
    """Keep a fresh page-1 request while rotating the other page into depth.

    Deep-only page windows can return no products for hours even while new
    clearance listings appear at the front. Retain the same two requests per
    keyword and the existing durable full-pass counter. Every pass fetches page
    one plus one page from 2-40. No extra API calls or parallel worker.
    """

    if CATALOG_MAX_PAGE <= 1:
        return (1,)
    deep_index = max(0, int(completed_passes)) % (CATALOG_MAX_PAGE - 1)
    return (1, 2 + deep_index)

def catalog_page_plan_for_claim(
    claim: GlobalCatalogClaim,
) -> tuple[tuple[str, int], ...]:
    """Four bounded API searches: three fresh routes plus one fair deep page.

    The previous plan requested page 1 and a deep page for only two catalog
    routes per minute. With the same four-request budget, this covers three
    routes per minute while retaining every deep page 2..40 over time.

    The extra deep request rotates between all catalog routes instead of
    privileging specific product names, stores or departments. Its schedule
    derives entirely from the durable cursor, including across lease retries
    and restarts, so it needs no extra writes or new persistent counters.
    """
    routes = tuple(claim.queries)
    if not routes:
        return ()
    plan = [(query, 1) for query in routes]
    if CATALOG_MAX_PAGE <= 1 or len(routes) != 3 or claim.total_routes < 3:
        return tuple(plan)

    # Across a wrapping three-route cursor, the relative route slot changes
    # by total_routes modulo three per full pass. Compensate for this so each
    # route receives exactly one deep request per three fresh revisits, even
    # when total_routes is not a multiple of three (currently ~265).
    remainder = claim.total_routes % 3
    if remainder:
        deep_slot = (
            remainder * max(0, claim.completed_passes_before)
            + claim.start_index
        ) % 3
    else:
        deep_slot = max(0, claim.completed_passes_before) % 3

    # Three fresh scans + one deep scan per batch. Each route receives one
    # deep visit per total_routes batches. This rotates that route across all
    # 39 deep pages rather than permanently skipping particular pages.
    batch_number = max(0, claim.completed_routes_before) // 3
    deep_page = 2 + (batch_number // claim.total_routes) % (CATALOG_MAX_PAGE - 1)
    plan.append((routes[deep_slot], deep_page))
    return tuple(plan)


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
    catalog_claim: GlobalCatalogClaim | None = None,
) -> WalmartCatalogDiscoveryResult:
    """Discover Walmart item IDs without doing foreground detail verification.

    Catalog search is candidate discovery only. Every candidate with a usable
    Walmart item ID is written to the durable exact-detail queue in one bulk
    operation. The dedicated queue worker is the sole owner of exact item,
    selected offer, seller, variant, fulfillment, current-price, and trusted
    reference-price verification.

    The global scan uses a four-request budget: page 1 for each of three
    freshly claimed routes plus one deeper page rotated fairly across routes.
    This retains deep coverage without doubling Walmart API traffic. Manual
    callers without a durable claim preserve the legacy two-page behavior.
    All scans stay global, independent of Discord server count.

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
    if catalog_claim is not None:
        if preset_queries != catalog_claim.queries:
            raise ValueError("Walmart catalog claim/preset routes differ")
        page_plan = catalog_page_plan_for_claim(catalog_claim)
        completed_passes = catalog_claim.completed_passes_before
    else:
        catalog_pages, completed_passes = await _current_catalog_pages(db)
        page_plan = tuple(
            (query, page)
            for query in preset_queries
            for page in catalog_pages
        )

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
        for query, page in page_plan
        for sort_value, order_value in CATALOG_SORT_PASSES
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

    # Report the actual bounded page requests, not a fictitious continuous
    # page range. Claim replay deliberately produces the same request plan.
    page_label = ",".join(str(page) for page in dict.fromkeys(
        page for _query, page in page_plan
    ))
    fresh_pages = sum(page == 1 for _query, page in page_plan)
    deep_pages = len(page_plan) - fresh_pages
    warnings.append(
        "Walmart catalog page window: "
        f"pages **{page_label}**/**{CATALOG_MAX_PAGE}** • "
        f"fresh routes **{fresh_pages}** • rotating deep requests **{deep_pages}** • "
        f"planned searches **{len(page_plan) * len(CATALOG_SORT_PASSES)}** • "
        f"completed full passes before this batch **{completed_passes}**."
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
