from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.providers.base import ProviderScanResult
from sniperplug.providers.registry import provider_registry
from sniperplug.services import verified_discount_hunt as hunt
from sniperplug.services.deal_finder_telemetry import (
    SearchRouteStats,
    merge_route_stats,
    tag_candidates_with_route,
)
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.deal_threshold_settings import (
    DEFAULT_STARTING_DEAL_PERCENT,
    get_starting_deal_percent,
    normalize_starting_deal_percent,
)
from sniperplug.services.low_price_scout import scout_low_price_leads
from sniperplug.services.walmart_exact_price_enrichment import (
    enrich_walmart_exact_prices,
    exact_detail_verified_candidates,
)
from sniperplug.services.walmart_observed_price_memory import (
    ObservedPriceMemorySelection,
    select_observed_price_drop_cards,
)
from sniperplug.services.walmart_price_memory import remembered_walmart_search_seeds
from sniperplug.services.walmart_review_candidates import (
    ReviewCandidateResult,
    build_review_candidate_cards,
)


AUTOSCAN_SEARCH_CONCURRENCY = 3
AUTOSCAN_PAGES_PER_QUERY = 2
AUTOSCAN_SORT_PASSES: tuple[tuple[str | None, str | None], ...] = ((None, None),)
AUTOSCAN_MEMORY_RECHECK_LIMIT = 4
AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES = 300
AUTOSCAN_EXACT_DETAIL_LIMIT = 24
AUTOSCAN_EXACT_DETAIL_CONCURRENCY = 4
AUTOSCAN_EXACT_DETAIL_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class AutoscanPriceMemorySelection:
    legacy: Any | None
    observed: ObservedPriceMemorySelection | None
    shown: list[Any]
    decisions: list[Any]

    def summary_line(self) -> str:
        if self.observed is None:
            return "global exact-offer price memory enabled, no products checked"
        observed_summary = self.observed.summary_line()
        examples = observed_drop_examples(self.observed.cards)
        if examples:
            observed_summary = f"{observed_summary} • examples: {examples}"
        return observed_summary


def observed_drop_examples(cards: list[Any], *, limit: int = 3) -> str:
    examples: list[str] = []
    for card in cards[: max(1, int(limit))]:
        label = compact_label(
            getattr(card, "label", None)
            or getattr(card, "title", None)
            or "observed drop"
        )
        discount = getattr(card, "api_discount_percent", None) or getattr(
            card, "discount", None
        )
        current = getattr(card, "api_current_price", None) or getattr(
            card, "current_price", None
        )
        bits = [label]
        if discount is not None:
            bits.append(f"{float(discount):.0f}% drop")
        if current is not None:
            bits.append(f"${float(current):,.2f}")
        examples.append(" (" + ", ".join(bits) + ")" if len(bits) > 1 else label)
    return ", ".join(examples)


def compact_label(value: Any, *, limit: int = 42) -> str:
    text = " ".join(str(value or "deal").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def collect_verified_discount_cards_with_observed_memory(
    *,
    requested_by: str,
    preset: HuntPreset | None = None,
    db=None,
    guild_id: int | None = None,
    use_price_memory: bool = False,
    min_discount: int | None = None,
) -> hunt.VerifiedHuntResult:
    """Discover broadly, then surface only official exact-detail Walmart rows.

    Search responses are candidate discovery only. Every card shown to users is
    rebuilt from Walmart's exact item-detail response before card construction.
    """

    preset = preset or hunt.ALL_VERIFIED_PRESET
    starting_discount = normalize_starting_deal_percent(
        min_discount
        if min_discount is not None
        else await get_starting_deal_percent(
            db,
            guild_id,
            fallback=DEFAULT_STARTING_DEAL_PERCENT,
        ),
        fallback=DEFAULT_STARTING_DEAL_PERCENT,
    )
    warnings: list[str] = []
    all_candidates = []
    route_stats: list[SearchRouteStats] = []
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(AUTOSCAN_SEARCH_CONCURRENCY)
    memory_seeds: tuple[str, ...] = ()
    if use_price_memory and db is not None and guild_id is not None:
        memory_seeds = await remembered_walmart_search_seeds(
            db,
            guild_id=guild_id,
            limit=AUTOSCAN_MEMORY_RECHECK_LIMIT,
        )
    preset_queries = tuple(hunt.dedupe_strings([*preset.queries, *memory_seeds]))

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
            return query, await deal_scanner.run_walmart_scan(
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
        for sort_value, order_value in AUTOSCAN_SORT_PASSES
        for page in range(1, AUTOSCAN_PAGES_PER_QUERY + 1)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        pages_checked += 1
        if isinstance(item, BaseException) or not isinstance(item, tuple) or len(item) != 2:
            warning_text = (
                str(item) or item.__class__.__name__
                if isinstance(item, BaseException)
                else f"bad Walmart route result: {type(item).__name__}"
            )
            if warning_text not in warnings:
                warnings.append(warning_text)
            route_stats.append(
                SearchRouteStats(
                    query="unknown",
                    pages_checked=1,
                    returned_products=0,
                    warnings=(warning_text,),
                )
            )
            continue

        query, result = item
        if not isinstance(result, ProviderScanResult):
            warning_text = f"bad Walmart provider result for {query}: {type(result).__name__}"
            if warning_text not in warnings:
                warnings.append(warning_text)
            route_stats.append(
                SearchRouteStats(
                    query=str(query or "unknown"),
                    pages_checked=1,
                    returned_products=0,
                    warnings=(warning_text,),
                )
            )
            continue

        candidates = list(result.candidates)
        tag_candidates_with_route(candidates, query=query)
        all_candidates.extend(candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)
        route_stats.append(
            SearchRouteStats(
                query=query,
                pages_checked=1,
                returned_products=len(candidates),
                warnings=tuple(result.warnings),
            )
        )

    deduped_candidates = deal_scanner.dedupe_candidates(all_candidates)
    exact_prices = await enrich_walmart_exact_prices(
        deduped_candidates,
        provider=provider_registry.get("walmart"),
        limit=AUTOSCAN_EXACT_DETAIL_LIMIT,
        concurrency=AUTOSCAN_EXACT_DETAIL_CONCURRENCY,
        timeout_seconds=AUTOSCAN_EXACT_DETAIL_TIMEOUT_SECONDS,
        min_discount=starting_discount,
    )
    deduped_candidates = exact_prices.candidates
    exact_candidates = exact_detail_verified_candidates(deduped_candidates)
    hidden_search_only = max(0, len(deduped_candidates) - len(exact_candidates))
    if hidden_search_only:
        warnings.append(
            "Official Walmart detail gate: "
            f"**{hidden_search_only}** search-only candidate(s) were kept out of cards."
        )
    if (
        exact_prices.attempted
        or exact_prices.identity_mismatches
        or exact_prices.failed
        or exact_prices.proofs_blocked
    ):
        warnings.append(exact_prices.summary_line())

    merged_route_stats = merge_route_stats(route_stats)
    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(exact_candidates),
        warnings=tuple(warnings),
        page=1,
        page_size=len(exact_candidates),
        start_index=1,
        has_next_page=True,
    )
    verified_cards = deal_scanner.build_walmart_cards(
        aggregate,
        min_discount=starting_discount,
        alerts_only=False,
    )
    verified_cards = rank_verified_cards(hunt.dedupe_cards(verified_cards))

    review_candidates = build_review_candidate_cards(
        exact_candidates,
        limit=hunt.REVIEW_LEAD_LIMIT,
    )
    scout_cards = scout_low_price_leads(
        exact_candidates,
        limit=hunt.REVIEW_LEAD_LIMIT,
        search_query="",
    )
    review_candidates = hunt.merge_review_and_scout_cards(
        review_candidates,
        scout_cards,
        limit=hunt.REVIEW_LEAD_LIMIT,
    )
    review_candidates = ReviewCandidateResult(
        cards=rank_review_cards(review_candidates.cards),
        under_threshold_count=review_candidates.under_threshold_count,
        missing_reference_count=review_candidates.missing_reference_count,
        weak_reference_count=review_candidates.weak_reference_count,
        missing_current_count=review_candidates.missing_current_count,
        no_value_signal_count=review_candidates.no_value_signal_count,
        rejected_bad_value_count=review_candidates.rejected_bad_value_count,
        exact_match_count=getattr(review_candidates, "exact_match_count", 0),
    )

    price_memory: AutoscanPriceMemorySelection | None = None
    cards = verified_cards
    if use_price_memory and db is not None and guild_id is not None:
        observed_memory = await select_observed_price_drop_cards(
            db,
            guild_id=guild_id,
            candidates=exact_candidates,
            min_discount=starting_discount,
            limit=5,
            max_observations=AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES,
        )
        memory_cards = hunt.dedupe_cards([*verified_cards, *observed_memory.cards])
        cards = rank_verified_cards(memory_cards)
        price_memory = AutoscanPriceMemorySelection(
            legacy=None,
            observed=observed_memory,
            shown=cards,
            decisions=list(observed_memory.decisions),
        )

    return hunt.VerifiedHuntResult(
        cards=cards,
        pages_checked=pages_checked,
        products_checked=len(all_candidates),
        warnings=warnings,
        searches_attempted=searches_attempted,
        min_discount=starting_discount,
        price_memory=price_memory,
        total_verified_cards=len(verified_cards),
        review_candidates=review_candidates,
        category_key=preset.key,
        route_stats=merged_route_stats,
        scout_lead_count=len(scout_cards),
        memory_recheck_count=len(memory_seeds),
    )


async def run_autoscan_verified_category_with_observed_memory(
    db,
    guild_id: int,
    *,
    preset: HuntPreset,
) -> hunt.VerifiedHuntResult:
    return await collect_verified_discount_cards_with_observed_memory(
        requested_by="autoscan",
        preset=preset,
        db=db,
        guild_id=guild_id,
        use_price_memory=True,
    )
