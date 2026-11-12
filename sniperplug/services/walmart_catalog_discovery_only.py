from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

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
from sniperplug.services.walmart_price_sniper_routes import (
    PRICE_SNIPER_ORDER,
    PRICE_SNIPER_QUERIES,
    PRICE_SNIPER_SORT,
    price_sniper_query_for_batch,
    priority_sniper_query_for_batch,
    priority_snipers_before_batch,
)


CATALOG_SEARCH_CONCURRENCY = 3
GLOBAL_CATALOG_REQUEST_BUDGET = 4
# Sniper-first split of the unchanged 4-searches/minute budget: 2 broad page-1
# routes + 2 price-sorted sniper searches. The real Walmart quota is unknown,
# so the total rate never rises; the trade-off is explicit and deliberate:
# sniper revisit 29 min -> ~16 min (29 rotation queries, ~9 rotation slots
# per 5 batches once the priority slot displaces one), broad rotation
# 89 min -> ~133 min (265 routes / 2 per batch). Money first: a $1 error on a
# phone/console/GPU is worth far more than re-sweeping a department page an
# hour sooner, and broad still advances every batch — it is slower, never
# starved.
BROAD_ROUTES_PER_BATCH = 2
SNIPER_SEARCHES_PER_BATCH = 2
CATALOG_PAGES_PER_QUERY = 2
CATALOG_PROVIDER_RESULT_LIMIT = 1000
CATALOG_MAX_PAGE = max(1, CATALOG_PROVIDER_RESULT_LIMIT // hunt.RESULTS_PER_PAGE)
CATALOG_SORT_PASSES: tuple[tuple[str | None, str | None], ...] = ((None, None),)
# One existing broad query per 12 completed batches tests the official
# bestseller search order. No fifth API call, no loss of either $1-price sniper.
# The provider labels this as *sort requested*, not confirmed recent purchases.
BESTSELLER_PROBE_EVERY_N_BATCHES = 12
BESTSELLER_PROBE_BATCH_OFFSET = 7


def is_bestseller_probe_batch(batch_number: int) -> bool:
    enabled = str(os.getenv("SNIPERPLUG_BESTSELLER_CANARY_ENABLED", "true")).strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return False
    return (
        max(0, int(batch_number)) % BESTSELLER_PROBE_EVERY_N_BATCHES
        == BESTSELLER_PROBE_BATCH_OFFSET
    )


@dataclass(frozen=True)
class WalmartCatalogDiscoveryResult:
    pages_checked: int = 0
    products_checked: int = 0
    searches_attempted: int = 0
    unique_candidates: int = 0
    candidates_with_item_id: int = 0
    warnings: tuple[str, ...] = ()
    bestseller_searches: int = 0
    bestseller_search_candidates: int = 0


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

CatalogRequest = tuple[str, int, "str | None", "str | None"]


def catalog_request_plan_for_claim(
    claim: GlobalCatalogClaim,
) -> tuple[CatalogRequest, ...]:
    """Four bounded API searches: two fresh routes plus two price snipers.

    Page one of each claimed catalog route (relevance order) keeps broad
    coverage, now at 2 routes/batch (~133 min for the 265-route pool instead
    of ~89). The other two requests are price-sorted (lowest first) searches
    for specific high-value products, where a price error appears at the top.

    The first sniper slot always runs the general rotation (29 queries,
    ~16 min revisit once the priority slot displaces one slot per 5 batches).
    The second sniper slot is the priority hot list (phones, consoles, GPUs)
    on every 5th batch, and the general rotation otherwise. The priority
    slot only ever replaces a sniper search, never a broad route, and is
    capped at 12/hour by construction.

    Both sniper queries derive from the durable claim cursor only, so a
    retried or replayed claim repeats exactly the same four requests and no
    new persistent state is needed. The rotation advances only by rotation
    slots actually spent, so a query displaced by a priority search is
    deferred to the next rotation slot, never skipped.
    """
    routes = tuple(claim.queries)
    if not routes:
        return ()
    plan: list[CatalogRequest] = [
        (query, 1, sort_value, order_value)
        for query in routes
        for sort_value, order_value in CATALOG_SORT_PASSES
    ]
    if len(routes) != BROAD_ROUTES_PER_BATCH or claim.total_routes < BROAD_ROUTES_PER_BATCH:
        return tuple(plan)
    batch_number = max(0, claim.completed_routes_before) // BROAD_ROUTES_PER_BATCH
    if is_bestseller_probe_batch(batch_number):
        # Reuse the second broad request, preserving its query and cursor. Its
        # position in an Affiliate response is not validated sales volume.
        plan[1] = (routes[1], 1, "bestseller", None)
    rotation_slots_before = (
        SNIPER_SEARCHES_PER_BATCH * batch_number
        - priority_snipers_before_batch(batch_number)
    )
    plan.append((
        price_sniper_query_for_batch(rotation_slots_before), 1,
        PRICE_SNIPER_SORT, PRICE_SNIPER_ORDER,
    ))
    priority_query = priority_sniper_query_for_batch(
        batch_number, avoid=plan[-1][0],
    )
    if priority_query is not None:
        second_sniper = priority_query
    else:
        second_sniper = price_sniper_query_for_batch(rotation_slots_before + 1)
    plan.append((
        second_sniper, 1,
        PRICE_SNIPER_SORT, PRICE_SNIPER_ORDER,
    ))
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

    The global scan uses a four-request budget: page 1 for each of two
    freshly claimed routes plus two price-sorted (lowest first) searches for
    rotating high-value products, where price errors surface. The second
    sniper slot is the priority hot list (phones, consoles, GPUs) every 5th
    batch. Manual callers without a durable claim preserve the legacy
    two-page behavior.
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
        request_plan = catalog_request_plan_for_claim(catalog_claim)
        completed_passes = catalog_claim.completed_passes_before
    else:
        catalog_pages, completed_passes = await _current_catalog_pages(db)
        request_plan = tuple(
            (query, page, sort_value, order_value)
            for query in preset_queries
            for page in catalog_pages
            for sort_value, order_value in CATALOG_SORT_PASSES
        )

    # Fail closed on accidental configuration changes that could multiply
    # vendor calls, DB reads and queued item rows.
    if (
        catalog_claim is not None
        and len(request_plan) > GLOBAL_CATALOG_REQUEST_BUDGET
    ):
        raise ValueError("global Walmart catalog request budget exceeded")

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
        for query, page, sort_value, order_value in request_plan
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

        # The API response must not amplify stored queue rows beyond the
        # bounded 25-result page requested from the provider.
        candidates = list(result.candidates[:hunt.RESULTS_PER_PAGE])
        if len(result.candidates) > hunt.RESULTS_PER_PAGE:
            warnings.append(
                "Walmart provider exceeded requested page size; "
                f"capped {len(result.candidates)} to {hunt.RESULTS_PER_PAGE}"
            )
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
        page for _query, page, _sort, _order in request_plan
    ))
    bestseller_searches = sum(sort_value == "bestseller" for _query, _page, sort_value, _order in request_plan)
    bestseller_search_candidates = sum(
        str((getattr(candidate, "variant_attributes", None) or {}).get("walmartBestsellerSortRequested") or "").lower() == "yes"
        for candidate in all_candidates
    )
    sniper_requests = sum(
        sort_value == PRICE_SNIPER_SORT for _query, _page, sort_value, _order in request_plan
    )
    fresh_pages = sum(
        page == 1 and sort_value != PRICE_SNIPER_SORT
        for _query, page, sort_value, _order in request_plan
    )
    deep_pages = len(request_plan) - fresh_pages - sniper_requests
    warnings.append(
        "Walmart catalog page window: "
        f"pages **{page_label}**/**{CATALOG_MAX_PAGE}** • "
        f"fresh routes **{fresh_pages}** • rotating deep requests **{deep_pages}** • "
        f"price-sorted sniper searches **{sniper_requests}** • "
        f"planned searches **{len(request_plan)}** • "
        f"completed full passes before this batch **{completed_passes}**."
    )
    if bestseller_searches:
        warnings.append(
            "Walmart bestseller canary: search sort requested **1** • "
            f"results **{bestseller_search_candidates}** • "
            "positions indicate search order, NOT verified purchases or sell-through."
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
        bestseller_searches=bestseller_searches,
        bestseller_search_candidates=bestseller_search_candidates,
    )
