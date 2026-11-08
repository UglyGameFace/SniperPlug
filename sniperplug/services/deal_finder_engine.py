from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, merge_route_stats, tag_candidates_with_route
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.deal_threshold_settings import DEFAULT_STARTING_DEAL_PERCENT
from sniperplug.services.deal_route_memory import RETAILER_WALMART, memory_boost_queries, record_route_memory, top_route_memory, update_from_route_stats
from sniperplug.services.direct_search_rescue import direct_match_score, meaningful_tokens, normalize_text
from sniperplug.services.low_price_scout import scout_low_price_leads
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.scan_result_accelerator import cached_provider_scan_or_run
from sniperplug.services.search_expansion import SearchPlan, expand_walmart_query
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_card, build_review_candidate_cards


QUERY_RESULTS_PER_PAGE = 25
QUERY_PAGES = 2
QUERY_CONCURRENCY = 4
INTERACTIVE_MAX_QUERIES = 5
INTERACTIVE_MAX_ROUTES = 8
DIRECT_PRODUCT_MATCH_MIN_SCORE = 0.66
DIRECT_PRODUCT_MATCH_LIMIT = 5


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
    boosted_routes: tuple[str, ...] = ()
    scout_lead_count: int = 0
    product_match_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    force_refresh: bool = False

    @property
    def has_any_cards(self) -> bool:
        return bool(self.verified_cards or self.review_candidates.cards)


def interactive_query_routes(
    plan: SearchPlan,
    *,
    pages_per_query: int = QUERY_PAGES,
    max_routes: int = INTERACTIVE_MAX_ROUTES,
) -> tuple[tuple[str, int, str | None, str | None], ...]:
    """Build a small, recall-aware route set for a user waiting in Discord.

    The exact user query gets two relevance pages plus price and bestseller
    coverage. Remaining route budget goes to the highest-priority expanded
    queries on page one. This deliberately prevents `/deals` from behaving like
    the deep `/discover` crawler.
    """
    queries = [str(value or "").strip() for value in plan.queries if str(value or "").strip()]
    if not queries:
        return ()

    bounded_routes = max(1, int(max_routes))
    primary_pages = max(1, min(2, int(pages_per_query)))
    routes: list[tuple[str, int, str | None, str | None]] = []

    def add(query: str, page: int, sort_value: str | None, order_value: str | None) -> None:
        route = (query, page, sort_value, order_value)
        if route not in routes and len(routes) < bounded_routes:
            routes.append(route)

    primary = queries[0]
    for page in range(1, primary_pages + 1):
        add(primary, page, None, None)
    add(primary, 1, "price", "ascending")
    add(primary, 1, "bestseller", None)

    for expanded in queries[1:]:
        add(expanded, 1, None, None)
        if len(routes) >= bounded_routes:
            break

    return tuple(routes)


async def find_walmart_deals_for_query(
    *,
    query: str,
    requested_by: str,
    min_discount: int = DEFAULT_STARTING_DEAL_PERCENT,
    max_queries: int = INTERACTIVE_MAX_QUERIES,
    pages_per_query: int = QUERY_PAGES,
    db=None,
    guild_id: int | None = None,
    force_refresh: bool = False,
) -> DealFinderResult:
    """Run a deep Walmart query search and return verified + private result cards.

    Uses a short DB-backed exact-route cache so repeated scans and mode buttons
    can reuse fresh provider responses without going blind to new glitches.
    `force_refresh=True` bypasses the cache for the explicit Fresh Scan path.

    Strong direct product matches stay private and can coexist with review/scout
    leads so a normal product search is not buried just because a weaker value
    lead also survived. Search relevance never becomes public deal proof.
    """
    memory_records = await top_route_memory(db, guild_id=guild_id, retailer=RETAILER_WALMART, limit=8)
    boosted_routes = memory_boost_queries(memory_records, limit=3)
    plan = expand_walmart_query(query, max_queries=max_queries, boosted_queries=boosted_routes)
    warnings: list[str] = []
    all_candidates = []
    route_stats: list[SearchRouteStats] = []
    pages_checked = 0
    searches_attempted = 0
    cache_hits = 0
    cache_misses = 0
    semaphore = asyncio.Semaphore(QUERY_CONCURRENCY)

    async def scan_one(search_query: str, page: int, sort_value: str | None, order_value: str | None) -> tuple[str, ProviderScanResult, bool]:
        nonlocal searches_attempted, cache_hits, cache_misses
        async with semaphore:
            outcome = await cached_provider_scan_or_run(
                db,
                retailer=RETAILER_WALMART,
                query=search_query,
                page=page,
                max_results=QUERY_RESULTS_PER_PAGE,
                sort_value=sort_value,
                order_value=order_value,
                force_refresh=force_refresh,
                runner=lambda: deal_scanner.run_walmart_scan(search_query, page, QUERY_RESULTS_PER_PAGE, sort_value, order_value, requested_by),
            )
            if outcome.cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1
                searches_attempted += 1
            return search_query, outcome.result, outcome.cache_hit

    routes = interactive_query_routes(
        plan,
        pages_per_query=pages_per_query,
        max_routes=INTERACTIVE_MAX_ROUTES,
    )
    tasks = [
        scan_one(search_query, page, sort_value, order_value)
        for search_query, page, sort_value, order_value in routes
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
        search_query, result, cache_hit = item
        candidates = list(result.candidates)
        tag_candidates_with_route(candidates, query=search_query)
        all_candidates.extend(candidates)
        route_warnings = tuple(result.warnings)
        warnings.extend(w for w in route_warnings if w not in warnings)
        if cache_hit:
            route_warnings = (*route_warnings, "cache hit")
        route_stats.append(SearchRouteStats(query=search_query, pages_checked=1, returned_products=len(candidates), warnings=route_warnings))
        total_results = result.total_results if result.total_results is not None else total_results
        has_next_page = has_next_page or bool(result.has_next_page)

    deduped = deal_scanner.dedupe_candidates(list(all_candidates))
    merged_route_stats = merge_route_stats(route_stats)
    await record_route_memory(
        db,
        guild_id=guild_id,
        retailer=RETAILER_WALMART,
        updates=update_from_route_stats(merged_route_stats),
    )
    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(deduped),
        warnings=tuple(warnings),
        total_results=total_results,
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=has_next_page,
        metadata={
            "query": query,
            "expanded_queries": ",".join(plan.queries),
            "cache_hits": str(cache_hits),
            "cache_misses": str(cache_misses),
            "force_refresh": "yes" if force_refresh else "no",
            "interactive_route_budget": str(len(routes)),
        },
    )
    verified = deal_scanner.build_walmart_cards(aggregate, min_discount=min_discount, alerts_only=False)
    verified = deal_scanner.dedupe_cards(verified) if hasattr(deal_scanner, "dedupe_cards") else _dedupe_cards(verified)
    verified = rank_verified_cards(verified)

    review = build_review_candidate_cards(list(deduped), query=query)
    scout_cards = scout_low_price_leads(deduped, limit=8, search_query=query)
    review = merge_scout_review_cards(review, scout_cards)
    direct_match_cards = build_direct_product_match_cards(
        deduped,
        query=query,
        limit=DIRECT_PRODUCT_MATCH_LIMIT,
    )
    review = apply_direct_product_fallback(
        verified_cards=verified,
        review=review,
        candidates=deduped,
        query=query,
        direct_match_cards=direct_match_cards,
    )
    review = merge_direct_product_matches(review, direct_match_cards)
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
        boosted_routes=boosted_routes,
        scout_lead_count=len(scout_cards),
        product_match_count=len(direct_match_cards),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        force_refresh=force_refresh,
    )


async def find_walmart_deals_for_preset(*, requested_by: str, preset: HuntPreset | None = None, db=None, guild_id: int | None = None, use_price_memory: bool = False):
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
    return await find_walmart_deals_for_preset(
        requested_by=requested_by,
        preset=None,
        db=db,
        guild_id=guild_id,
        use_price_memory=use_price_memory,
    )


def apply_direct_product_fallback(
    *,
    verified_cards: list[DealCard],
    review: ReviewCandidateResult,
    candidates: list[SourceCandidate],
    query: str,
    limit: int = DIRECT_PRODUCT_MATCH_LIMIT,
    direct_match_cards: list[DealCard] | None = None,
) -> ReviewCandidateResult:
    """Use private direct product matches when every deal lane is empty."""
    if verified_cards or review.cards:
        return review
    fallback_cards = direct_match_cards
    if fallback_cards is None:
        fallback_cards = build_direct_product_match_cards(candidates, query=query, limit=limit)
    if not fallback_cards:
        return review
    return copy_review_result(review, cards=fallback_cards[: max(1, int(limit))])


def build_direct_product_match_cards(
    candidates: list[SourceCandidate],
    *,
    query: str,
    limit: int = DIRECT_PRODUCT_MATCH_LIMIT,
    min_match_score: float = DIRECT_PRODUCT_MATCH_MIN_SCORE,
) -> list[DealCard]:
    """Render closest current-price Walmart matches without inventing a deal."""
    scored: list[tuple[float, int, DealCard]] = []
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        deal = candidate.to_normalized_deal()
        if deal.current_price is None or deal.current_price <= 0 or not deal.product_url or not deal.title:
            continue
        match_score = private_product_match_score(
            query,
            deal.title,
            sku=deal.sku,
            upc=deal.upc,
            product_id=candidate.product_id,
        )
        if match_score < min_match_score:
            continue
        key = candidate.selected_offer_id or candidate.product_id or deal.sku or deal.upc or deal.product_url or deal.title
        if key in seen:
            continue
        seen.add(key)

        proof = verified_deal_value(deal)
        card = build_review_card(
            candidate,
            deal,
            proof,
            context_price=None,
            context_discount=None,
            ignored_context_price=None,
            coupon=0.0,
            cash=0.0,
            direct_match_score=match_score,
        )
        card.should_alert = False
        card.manual_share_allowed = False
        card.product_search_match = True
        card.product_match_score = match_score
        card.deal_lane = "product_search_match"
        scored.append((match_score, -index, card))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [card for _, _, card in scored[: max(1, int(limit))]]


def private_product_match_score(
    query: str,
    title: str,
    *,
    sku: str | None = None,
    upc: str | None = None,
    product_id: str | None = None,
) -> float:
    """Private product-search relevance; never used as public deal proof."""
    score = direct_match_score(query, title, sku=sku, upc=upc, product_id=product_id)
    if score > 0:
        return score

    query_text = normalize_text(query)
    title_text = normalize_text(title)
    identifiers = {normalize_text(value) for value in (sku, upc, product_id) if value}
    if query_text and query_text in identifiers:
        return 1.0

    query_tokens = meaningful_tokens(query_text)
    title_tokens = meaningful_tokens(title_text)
    if len(query_tokens) == 1 and query_tokens.issubset(title_tokens):
        return 1.0
    return 0.0


def copy_review_result(review: ReviewCandidateResult, *, cards: list[DealCard]) -> ReviewCandidateResult:
    return ReviewCandidateResult(
        cards=cards,
        under_threshold_count=review.under_threshold_count,
        missing_reference_count=review.missing_reference_count,
        weak_reference_count=review.weak_reference_count,
        missing_current_count=review.missing_current_count,
        no_value_signal_count=review.no_value_signal_count,
        rejected_bad_value_count=review.rejected_bad_value_count,
        exact_match_count=review.exact_match_count,
        ignored_reference_sources=review.ignored_reference_sources,
        no_value_reasons=review.no_value_reasons,
    )


def merge_direct_product_matches(
    review: ReviewCandidateResult,
    direct_match_cards: list[DealCard],
    *,
    limit: int = 10,
) -> ReviewCandidateResult:
    """Keep strong private search matches even when review/scout leads also exist."""
    if not direct_match_cards:
        return review
    merged: list[DealCard] = []
    seen: set[str] = set()
    for card in [*direct_match_cards, *review.cards]:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url or card.label
        if key in seen:
            continue
        seen.add(key)
        merged.append(card)
    return copy_review_result(review, cards=merged[: max(1, int(limit))])


def merge_scout_review_cards(review: ReviewCandidateResult, scout_cards: list[DealCard], *, limit: int = 10) -> ReviewCandidateResult:
    merged: list[DealCard] = []
    seen: set[str] = set()
    for card in [*review.cards, *scout_cards]:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url or card.label
        if key in seen:
            continue
        seen.add(key)
        merged.append(card)
    return ReviewCandidateResult(
        cards=merged[:limit],
        under_threshold_count=review.under_threshold_count,
        missing_reference_count=review.missing_reference_count,
        weak_reference_count=review.weak_reference_count,
        missing_current_count=review.missing_current_count,
        no_value_signal_count=review.no_value_signal_count,
        rejected_bad_value_count=review.rejected_bad_value_count,
        exact_match_count=getattr(review, "exact_match_count", 0),
        ignored_reference_sources=review.ignored_reference_sources,
        no_value_reasons=review.no_value_reasons,
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
        exact_match_count=getattr(review, "exact_match_count", 0),
        ignored_reference_sources=review.ignored_reference_sources,
        no_value_reasons=review.no_value_reasons,
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
