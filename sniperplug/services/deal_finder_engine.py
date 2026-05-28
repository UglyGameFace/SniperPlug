from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, merge_route_stats, tag_candidates_with_route
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.search_expansion import SearchPlan, expand_walmart_query
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards


QUERY_RESULTS_PER_PAGE = 25
QUERY_PAGES = 2
QUERY_CONCURRENCY = 5
QUERY_SORT_PASSES: tuple[tuple[str | None, str | None], ...] = (
    (None, None),
    ("price", "ascending"),
)


@dataclass(frozen=True)
class DealFinderResult:
    query: str
    search_plan: SearchPlan
    aggregate: ProviderScanResult
    verified_cards: list[DealCard]
    review_candidates: ReviewCandidateResult
    pages_checked: int
    products_checked: int
    warnings: list[str]
    searches_attempted: int
    min_discount: int
    route_stats: tuple[SearchRouteStats, ...] = ()

    @property
    def has_any_cards(self) -> bool:
        return bool(self.verified_cards or self.review_candidates.cards)


async def find_walmart_deals_for_query(*, query: str, requested_by: str, min_discount: int = 50, max_queries: int = 5, pages_per_query: int = QUERY_PAGES) -> DealFinderResult:
    """Run an expanded Walmart query search and return verified + review/flip cards.

    This is the shared engine path for `/deals` first, and can be reused by
    category/discovery commands as they migrate away from one-off scan code.
    """
    plan = expand_walmart_query(query, max_queries=max_queries)
    warnings: list[str] = []
    all_candidates = []
    route_stats: list[SearchRouteStats] = []
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(QUERY_CONCURRENCY)

    async def scan_one(search_query: str, page: int, sort_value: str | None, order_value: str | None) -> tuple[str, ProviderScanResult]:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            return search_query, await deal_scanner.run_walmart_scan(search_query, page, QUERY_RESULTS_PER_PAGE, sort_value, order_value, requested_by)

    tasks = [
        scan_one(search_query, page, sort_value, order_value)
        for search_query in plan.queries
        for sort_value, order_value in QUERY_SORT_PASSES
        for page in range(1, pages_per_query + 1)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
    total_results: int | None = None
    has_next_page = False
    for item in results:
        pages_checked += 1
        if isinstance(item, Exception):
            text = str(item) or item.__class__.__name__
            if text not in warnings:
                warnings.append(text)
            route_stats.append(SearchRouteStats(query="unknown", pages_checked=1, returned_products=0, warnings=(text,)))
            continue
        search_query, result = item
        candidates = list(result.candidates)
        tag_candidates_with_route(candidates, query=search_query)
        all_candidates.extend(candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)
        route_stats.append(SearchRouteStats(query=search_query, pages_checked=1, returned_products=len(candidates), warnings=tuple(result.warnings)))
        total_results = result.total_results if result.total_results is not None else total_results
        has_next_page = has_next_page or bool(result.has_next_page)

    deduped = deal_scanner.dedupe_candidates(list(all_candidates))
    merged_route_stats = merge_route_stats(route_stats)
    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(deduped),
        warnings=tuple(warnings),
        total_results=total_results,
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=has_next_page,
        metadata={"query": query, "expanded_queries": ",".join(plan.queries)},
    )
    verified = deal_scanner.build_walmart_cards(aggregate, min_discount=min_discount, alerts_only=False)
    verified = deal_scanner.dedupe_cards(verified) if hasattr(deal_scanner, "dedupe_cards") else _dedupe_cards(verified)
    verified = rank_verified_cards(verified)

    review = build_review_candidate_cards(list(deduped))
    review = rank_review_candidate_result(review)

    return DealFinderResult(
        query=query,
        search_plan=plan,
        aggregate=aggregate,
        verified_cards=verified,
        review_candidates=review,
        pages_checked=pages_checked,
        products_checked=len(all_candidates),
        warnings=warnings,
        searches_attempted=searches_attempted,
        min_discount=min_discount,
        route_stats=merged_route_stats,
    )


async def find_walmart_deals_for_preset(*, requested_by: str, preset: HuntPreset | None = None, db=None, guild_id: int | None = None, use_price_memory: bool = False):
    """Shared engine wrapper for category/broad Walmart hunts.

    This intentionally delegates scan collection to the existing verified hunt
    implementation, then applies the same ranking normalization used by `/deals`.
    """
    from sniperplug.services.verified_discount_hunt import VerifiedHuntResult, collect_verified_discount_cards

    result = await collect_verified_discount_cards(
        requested_by=requested_by,
        preset=preset,
        db=db,
        guild_id=guild_id,
        use_price_memory=use_price_memory,
    )
    review = rank_review_candidate_result(result.review_candidates) if result.review_candidates else None
    cards = rank_verified_cards(result.cards)
    return VerifiedHuntResult(
        cards=cards,
        pages_checked=result.pages_checked,
        products_checked=result.products_checked,
        warnings=result.warnings,
        searches_attempted=result.searches_attempted,
        min_discount=result.min_discount,
        price_memory=result.price_memory,
        total_verified_cards=result.total_verified_cards,
        review_candidates=review,
        category_key=result.category_key,
        route_stats=result.route_stats,
    )


async def find_walmart_discovery_deals(*, requested_by: str, db=None, guild_id: int | None = None, use_price_memory: bool = False):
    """Shared broad discovery entrypoint used by `/discover` and future schedulers."""
    return await find_walmart_deals_for_preset(
        requested_by=requested_by,
        preset=None,
        db=db,
        guild_id=guild_id,
        use_price_memory=use_price_memory,
    )


def rank_review_candidate_result(review: ReviewCandidateResult) -> ReviewCandidateResult:
    return ReviewCandidateResult(
        cards=rank_review_cards(review.cards),
        under_threshold_count=review.under_threshold_count,
        missing_reference_count=review.missing_reference_count,
        weak_reference_count=review.weak_reference_count,
        missing_current_count=review.missing_current_count,
        no_value_signal_count=review.no_value_signal_count,
        rejected_bad_value_count=review.rejected_bad_value_count,
    )


def _dedupe_cards(cards: list[DealCard]) -> list[DealCard]:
    seen: set[str] = set()
    unique: list[DealCard] = []
    for card in cards:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url or card.label
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique
