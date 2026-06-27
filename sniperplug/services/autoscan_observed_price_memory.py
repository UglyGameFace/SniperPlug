from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.autoscan_no_post_intelligence import build_autoscan_no_post_intelligence
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, merge_route_stats, tag_candidates_with_route
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.deal_threshold_settings import DEFAULT_STARTING_DEAL_PERCENT, get_starting_deal_percent, normalize_starting_deal_percent
from sniperplug.services.low_price_scout import scout_low_price_leads
from sniperplug.services.walmart_observed_price_memory import ObservedPriceMemorySelection, select_observed_price_drop_cards
from sniperplug.services.walmart_price_memory import PriceMemorySelection, remembered_walmart_search_seeds, select_price_intelligent_cards
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards
from sniperplug.services import verified_discount_hunt as hunt


@dataclass(frozen=True)
class AutoscanPriceMemorySelection:
    legacy: PriceMemorySelection | None
    observed: ObservedPriceMemorySelection | None
    shown: list[Any]
    decisions: list[Any]

    def summary_line(self) -> str:
        pieces: list[str] = []
        if self.legacy is not None:
            try:
                pieces.append(f"legacy verified-card memory: {self.legacy.summary_line()}")
            except Exception:
                pieces.append("legacy verified-card memory used")
        if self.observed is not None:
            observed_summary = self.observed.summary_line()
            examples = observed_drop_examples(self.observed.cards)
            if examples:
                observed_summary = f"{observed_summary} • examples: {examples}"
            pieces.append(observed_summary)
        if not pieces:
            return "price memory enabled, no products checked"
        return " • ".join(pieces)


def observed_drop_examples(cards: list[Any], *, limit: int = 3) -> str:
    examples: list[str] = []
    for card in cards[: max(1, int(limit))]:
        label = compact_label(getattr(card, "label", None) or getattr(card, "title", None) or "observed drop")
        discount = getattr(card, "api_discount_percent", None) or getattr(card, "discount", None)
        current = getattr(card, "api_current_price", None) or getattr(card, "current_price", None)
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
    """Autoscan collector with exact-item observed price memory enabled.

    This mirrors the verified hunt scan path but also records every exact
    Walmart candidate in price memory, so future scans can public-post a real
    observed price drop even when Walmart does not provide clean was/reference
    fields.
    """

    preset = preset or hunt.ALL_VERIFIED_PRESET
    starting_discount = normalize_starting_deal_percent(
        min_discount if min_discount is not None else await get_starting_deal_percent(db, guild_id, fallback=DEFAULT_STARTING_DEAL_PERCENT),
        fallback=DEFAULT_STARTING_DEAL_PERCENT,
    )
    warnings: list[str] = []
    all_candidates = []
    route_stats: list[SearchRouteStats] = []
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(hunt.SCAN_CONCURRENCY)
    memory_seeds: tuple[str, ...] = ()
    if use_price_memory and db is not None and guild_id is not None:
        memory_seeds = await remembered_walmart_search_seeds(db, guild_id=guild_id, limit=hunt.MEMORY_RECHECK_LIMIT)
    preset_queries = tuple(hunt.dedupe_strings([*preset.queries, *memory_seeds]))

    async def scan_one(query: str, page: int, sort_value: str | None, order_value: str | None) -> tuple[str, ProviderScanResult]:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            return query, await deal_scanner.run_walmart_scan(query, page, hunt.RESULTS_PER_PAGE, sort_value, order_value, requested_by)

    tasks = [
        scan_one(query, page, sort_value, order_value)
        for query in preset_queries
        for sort_value, order_value in hunt.SORT_PASSES
        for page in range(1, hunt.PAGES_PER_QUERY + 1)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        pages_checked += 1
        if isinstance(item, BaseException) or not isinstance(item, tuple) or len(item) != 2:
            warning_text = str(item) or item.__class__.__name__ if isinstance(item, BaseException) else f"bad Walmart route result: {type(item).__name__}"
            if warning_text not in warnings:
                warnings.append(warning_text)
            route_stats.append(SearchRouteStats(query="unknown", pages_checked=1, returned_products=0, warnings=(warning_text,)))
            continue

        query, result = item
        if not isinstance(result, ProviderScanResult):
            warning_text = f"bad Walmart provider result for {query}: {type(result).__name__}"
            if warning_text not in warnings:
                warnings.append(warning_text)
            route_stats.append(SearchRouteStats(query=str(query or "unknown"), pages_checked=1, returned_products=0, warnings=(warning_text,)))
            continue

        candidates = list(result.candidates)
        tag_candidates_with_route(candidates, query=query)
        all_candidates.extend(candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)
        route_stats.append(SearchRouteStats(query=query, pages_checked=1, returned_products=len(candidates), warnings=tuple(result.warnings)))

    deduped_candidates = deal_scanner.dedupe_candidates(all_candidates)
    merged_route_stats = merge_route_stats(route_stats)
    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(deduped_candidates),
        warnings=tuple(warnings),
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=True,
    )
    verified_cards = deal_scanner.build_walmart_cards(aggregate, min_discount=starting_discount, alerts_only=False)
    verified_cards = rank_verified_cards(hunt.dedupe_cards(verified_cards))

    review_candidates = build_review_candidate_cards(list(deduped_candidates), limit=hunt.REVIEW_LEAD_LIMIT)
    scout_cards = scout_low_price_leads(deduped_candidates, limit=hunt.REVIEW_LEAD_LIMIT, search_query="")
    review_candidates = hunt.merge_review_and_scout_cards(review_candidates, scout_cards, limit=hunt.REVIEW_LEAD_LIMIT)
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

    price_memory: AutoscanPriceMemorySelection | PriceMemorySelection | None = None
    cards = verified_cards
    if use_price_memory and db is not None and guild_id is not None:
        observed_memory = await select_observed_price_drop_cards(
            db,
            guild_id=guild_id,
            candidates=list(deduped_candidates),
            min_discount=starting_discount,
            limit=5,
        )
        legacy_memory = await select_price_intelligent_cards(
            db,
            guild_id=guild_id,
            cards=verified_cards,
            fallback_retailer="walmart",
            limit=None,
        )
        memory_cards = hunt.dedupe_cards([*legacy_memory.shown, *observed_memory.cards])
        cards = rank_verified_cards(memory_cards)
        price_memory = AutoscanPriceMemorySelection(
            legacy=legacy_memory,
            observed=observed_memory,
            shown=cards,
            decisions=[*getattr(legacy_memory, "decisions", []), *getattr(observed_memory, "decisions", [])],
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


async def run_autoscan_verified_category_with_observed_memory(db, guild_id: int, *, preset: HuntPreset) -> hunt.VerifiedHuntResult:
    return await collect_verified_discount_cards_with_observed_memory(
        requested_by="autoscan",
        preset=preset,
        db=db,
        guild_id=guild_id,
        use_price_memory=True,
    )


def install_autoscan_observed_price_memory() -> None:
    """Route background autoscan through observed price-memory proof.

    Only the autoscan category runner is redirected. Manual hunts and public
    posting gates stay unchanged.
    """

    from sniperplug.cogs import auto_scan_runner

    if getattr(auto_scan_runner, "_sniperplug_observed_price_memory_installed", False):
        return
    auto_scan_runner.run_autoscan_verified_category = run_autoscan_verified_category_with_observed_memory
    auto_scan_runner.autoscan_blocker_summary = build_autoscan_no_post_intelligence
    auto_scan_runner._sniperplug_observed_price_memory_installed = True
