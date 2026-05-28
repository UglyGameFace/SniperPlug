from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.providers.base import ProviderScanResult
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
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(QUERY_CONCURRENCY)

    async def scan_one(search_query: str, page: int, sort_value: str | None, order_value: str | None) -> ProviderScanResult:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            return await deal_scanner.run_walmart_scan(search_query, page, QUERY_RESULTS_PER_PAGE, sort_value, order_value, requested_by)

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
            continue
        all_candidates.extend(item.candidates)
        warnings.extend(w for w in item.warnings if w not in warnings)
        total_results = item.total_results if item.total_results is not None else total_results
        has_next_page = has_next_page or bool(item.has_next_page)

    deduped = deal_scanner.dedupe_candidates(list(all_candidates))
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
    ranked_review_cards = rank_review_cards(review.cards)
    review = ReviewCandidateResult(
        cards=ranked_review_cards,
        under_threshold_count=review.under_threshold_count,
        missing_reference_count=review.missing_reference_count,
        weak_reference_count=review.weak_reference_count,
        missing_current_count=review.missing_current_count,
        no_value_signal_count=review.no_value_signal_count,
        rejected_bad_value_count=review.rejected_bad_value_count,
    )

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
