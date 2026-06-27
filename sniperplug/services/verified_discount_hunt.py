from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.embed_delivery import batch_cards_for_limit, sanitize_embed
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, merge_route_stats, tag_candidates_with_route, top_route_lines
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.deal_threshold_settings import DEFAULT_STARTING_DEAL_PERCENT, get_starting_deal_percent, normalize_starting_deal_percent
from sniperplug.services.deal_category_preferences import apply_category_preferences, get_category_preferences
from sniperplug.services.low_price_scout import scout_low_price_leads
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.walmart_observed_price_memory import ObservedPriceMemorySelection, select_observed_price_drop_cards
from sniperplug.services.walmart_price_memory import PriceMemorySelection, remembered_walmart_search_seeds, select_price_intelligent_cards
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards


TRUE_DISCOUNT_MIN = 50  # legacy/default constant kept for old tests/imports
RESULTS_PER_PAGE = 25
PAGES_PER_QUERY = 5
SCAN_CONCURRENCY = 6
MEMORY_RECHECK_LIMIT = 30
REVIEW_LEAD_LIMIT = 25
ALL_VERIFIED_HUNT_KEY = "all_verified_discounts"

CATEGORY_ROUTES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "all": (
        "All Walmart",
        "🚨",
        "All major Walmart sale, department, and low-price value surfaces.",
        (
            "clearance",
            "rollback",
            "price drop",
            "reduced price",
            "special buy",
            "walmart deals",
            "walmart cash eligible",
            "walmart cash offers",
            "walmart cash",
            "onepay cash rewards",
            "cash back walmart",
            "online clearance",
            "overstock clearance",
            "wireless charging station",
            "wireless charger rollback",
            "phone accessories clearance",
            "magsafe charger",
            "power bank rollback",
            "desk gadget",
            "smart home clearance",
            "office chair clearance",
            "pet supplies clearance",
            "shoe clearance",
            "seasonal clearance",
            "electronics clearance",
            "phone clearance",
            "prepaid phone clearance",
            "straight talk phone",
            "open box electronics",
            "restored electronics",
            "toy clearance",
            "lego clearance",
            "home clearance",
            "kitchen clearance",
            "appliance clearance",
            "tool clearance",
            "auto clearance",
            "motor oil rollback",
            "household clearance",
            "grocery clearance",
            "laundry detergent rollback",
            "paper goods rollback",
            "personal care clearance",
            "baby clearance",
            "pet clearance",
            "beauty clearance",
            "fragrance",
            "fragrance clearance",
            "designer fragrance",
            "designer fragrance clearance",
            "designer cologne",
            "cologne",
            "cologne clearance",
            "men cologne",
            "men cologne clearance",
            "perfume",
            "perfume clearance",
            "eau de parfum",
            "eau de toilette",
            "dolce gabbana cologne",
            "versace cologne",
            "gucci perfume",
            "ysl fragrance",
            "armani cologne",
            "calvin klein fragrance",
            "apparel clearance",
            "shoe clearance",
            "jewelry clearance",
            "sporting goods clearance",
            "outdoor clearance",
            "office clearance",
        ),
    ),
    "deal_week": (
        "Walmart Deal Week Watchlist",
        "🔥",
        "High-signal Walmart sale-week searches for exact products, brands, and flip categories SniperPlug should not miss.",
        (
            "walmart deal week",
            "walmart deals",
            "walmart cash eligible",
            "walmart cash offers",
            "walmart cash beauty",
            "walmart cash personal care",
            "walmart cash household",
            "walmart cash grocery",
            "walmart deals electronics",
            "walmart rollback deals",
            "walmart clearance deals",
            "walmart special buy",
            "gaming monitor deals",
            "gaming monitor rollback",
            "gaming monitor clearance",
            "computer monitor deals",
            "computer monitor rollback",
            "24 inch monitor rollback",
            "27 inch monitor rollback",
            "32 inch monitor rollback",
            "dell monitor deals",
            "sceptre monitor deals",
            "onn monitor deals",
            "samsung monitor rollback",
            "lg monitor rollback",
            "gaming headset deals",
            "wireless gaming headset rollback",
            "hyperx headset rollback",
            "razer headset rollback",
            "logitech headset rollback",
            "corsair headset rollback",
            "gaming keyboard mouse combo",
            "mechanical keyboard rollback",
            "ssd rollback",
            "nvme ssd rollback",
            "external hard drive rollback",
            "4k tv rollback",
            "smart tv deals",
            "onn tv deals",
            "samsung tv rollback",
            "tcl tv rollback",
            "roku tv rollback",
            "tablet rollback",
            "laptop deals",
            "laptop rollback",
            "chromebook rollback",
            "prepaid phone clearance",
            "straight talk phone deals",
            "lego deals",
            "lego clearance",
            "pokemon cards deals",
            "pokemon clearance",
            "board game clearance",
            "video game clearance",
            "collectible clearance",
            "air fryer rollback",
            "ninja air fryer rollback",
            "vacuum rollback",
            "shark vacuum rollback",
            "coffee maker rollback",
            "keurig rollback",
            "patio furniture clearance",
            "furniture clearance",
            "mattress rollback",
            "tool rollback",
            "tool clearance",
            "hart tools deals",
            "hart tools clearance",
            "dewalt rollback",
            "dewalt clearance",
            "milwaukee clearance",
            "hyper tough tools deals",
            "tire inflator rollback",
            "motor oil rollback",
            "synthetic motor oil rollback",
            "mobil 1 rollback",
            "castrol rollback",
            "car care deals",
            "wireless charging station",
            "wireless charger rollback",
            "3 in 1 charger",
            "3-in-1 wireless charger",
            "magsafe charger",
            "iphone charger rollback",
            "apple watch charger",
            "airpods charger",
            "charging dock",
            "charging stand",
            "phone accessories clearance",
            "phone accessories rollback",
            "usb c charger rollback",
            "power bank rollback",
            "anker charger",
            "belkin charger",
            "car phone mount",
            "desk gadget",
            "desk organizer rollback",
            "smart home clearance",
            "security camera rollback",
            "doorbell camera rollback",
            "smart plug rollback",
            "smart bulb rollback",
            "robot vacuum rollback",
            "dash cam rollback",
            "projector rollback",
            "led light rollback",
            "viral gadget",
            "massage gun rollback",
            "office chair rollback",
            "office chair clearance",
            "desk rollback",
            "standing desk clearance",
            "printer rollback",
            "ink cartridge rollback",
            "toner rollback",
            "laptop stand rollback",
            "monitor arm rollback",
            "school supplies clearance",
            "laundry detergent rollback",
            "tide rollback",
            "paper towels rollback",
            "toilet paper rollback",
            "trash bags rollback",
            "cleaning supplies rollback",
            "diaper rollback",
            "baby wipes rollback",
            "baby monitor rollback",
            "pet supplies clearance",
            "dog food rollback",
            "cat litter rollback",
            "shoe clearance",
            "sneaker clearance",
            "nike shoes walmart",
            "jacket clearance",
            "hoodie clearance",
            "bike rollback",
            "electric scooter rollback",
            "cooler rollback",
            "camping clearance",
            "grill clearance",
            "pool clearance",
            "seasonal clearance",
            "holiday clearance",
            "designer cologne clearance",
            "designer fragrance clearance",
            "dolce gabbana cologne",
            "dolce gabbana the one",
            "dolce gabbana the one men",
            "versace cologne",
            "armani cologne",
            "calvin klein cologne",
            "gold chain",
            "gold chain clearance",
            "jewelry clearance",
            "mens jewelry clearance",
        ),
    ),
    "tech": ("Tech & Gaming", "🎮", "Electronics, gaming, TVs, monitors, phones, laptops, and restored tech.", ("electronics clearance", "electronics rollback", "gaming clearance", "laptop clearance", "tv clearance", "monitor clearance", "phone clearance", "prepaid phone clearance", "straight talk phone", "open box electronics", "restored electronics", "refurbished laptop")),
    "beauty": ("Beauty & Fragrance", "💄", "Perfume, cologne, designer fragrance, grooming, and beauty value leads.", ("beauty clearance", "fragrance", "fragrance clearance", "designer fragrance", "designer fragrance clearance", "designer cologne", "designer perfume", "cologne", "cologne clearance", "men cologne", "men cologne clearance", "perfume", "perfume clearance", "eau de parfum", "eau de parfum clearance", "eau de toilette", "eau de toilette clearance", "dolce gabbana cologne", "versace cologne", "gucci perfume", "ysl fragrance", "armani cologne", "calvin klein fragrance", "premium beauty clearance")),
    "home": ("Home & Kitchen", "🏠", "Kitchen, home, furniture, patio, appliances, and storage.", ("home clearance", "home rollback", "kitchen clearance", "appliance clearance", "furniture clearance", "patio clearance", "vacuum clearance", "air fryer clearance", "coffee maker clearance", "mattress clearance")),
    "toys": ("Toys & Gifts", "🧸", "Toys, LEGO, games, collectibles, and giftable markdowns.", ("toy clearance", "toy rollback", "lego clearance", "pokemon cards", "board game clearance", "collectible clearance", "video game clearance", "barbie clearance")),
    "auto_tools": ("Auto & Tools", "🛠️", "Tools, garage, car care, oil, and DIY markdowns.", ("tool clearance", "tool rollback", "auto clearance", "drill clearance", "dewalt clearance", "milwaukee clearance", "hart tools clearance", "pressure washer clearance", "car care clearance", "motor oil rollback")),
    "essentials": ("Daily Essentials", "🧼", "Household, grocery, personal care, baby, pet, and coupon/cash value checks.", ("walmart cash eligible", "walmart cash offers", "household clearance", "household rollback", "grocery clearance", "cleaning supplies clearance", "laundry detergent rollback", "paper goods rollback", "toilet paper rollback", "personal care clearance", "diaper clearance", "baby clearance", "pet clearance")),
}

DISCOVERY_QUERIES = CATEGORY_ROUTES["all"][3]

SORT_PASSES: tuple[tuple[str | None, str | None], ...] = ((None, None), ("price", "ascending"))

HUNT_PRESETS: dict[str, HuntPreset] = {key: HuntPreset(key, label, emoji, description, queries, TRUE_DISCOUNT_MIN) for key, (label, emoji, description, queries) in CATEGORY_ROUTES.items()}
ALL_VERIFIED_PRESET = HuntPreset(ALL_VERIFIED_HUNT_KEY, CATEGORY_ROUTES["all"][0], CATEGORY_ROUTES["all"][1], CATEGORY_ROUTES["all"][2], DISCOVERY_QUERIES, TRUE_DISCOUNT_MIN)


@dataclass(frozen=True)
class VerifiedHuntResult:
    cards: list[DealCard]
    pages_checked: int
    products_checked: int
    warnings: list[str]
    searches_attempted: int
    min_discount: int = TRUE_DISCOUNT_MIN
    price_memory: PriceMemorySelection | None = None
    observed_price_memory: ObservedPriceMemorySelection | None = None
    total_verified_cards: int = 0
    review_candidates: ReviewCandidateResult | None = None
    category_key: str = "all"
    route_stats: tuple[SearchRouteStats, ...] = ()
    scout_lead_count: int = 0
    memory_recheck_count: int = 0


async def run_verified_discount_hunt(preset: HuntPreset | None = None, requested_by: str = "") -> tuple[list[DealCard], int, int, list[str], int]:
    result = await collect_verified_discount_cards(preset=preset or ALL_VERIFIED_PRESET, requested_by=requested_by)
    return result.cards, result.pages_checked, result.products_checked, result.warnings, result.min_discount


async def collect_verified_discount_cards(*, requested_by: str, preset: HuntPreset | None = None, db=None, guild_id: int | None = None, use_price_memory: bool = False, min_discount: int | None = None) -> VerifiedHuntResult:
    preset = preset or ALL_VERIFIED_PRESET
    starting_discount = normalize_starting_deal_percent(min_discount if min_discount is not None else await get_starting_deal_percent(db, guild_id, fallback=DEFAULT_STARTING_DEAL_PERCENT), fallback=DEFAULT_STARTING_DEAL_PERCENT)
    warnings: list[str] = []
    all_candidates: list[SourceCandidate] = []
    route_stats: list[SearchRouteStats] = []
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
    memory_seeds: tuple[str, ...] = ()
    if use_price_memory and db is not None and guild_id is not None:
        memory_seeds = await remembered_walmart_search_seeds(db, guild_id=guild_id, limit=MEMORY_RECHECK_LIMIT)
    preset_queries = tuple(dedupe_strings([*preset.queries, *memory_seeds]))

    async def scan_one(query: str, page: int, sort_value: str | None, order_value: str | None) -> tuple[str, ProviderScanResult]:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            return query, await deal_scanner.run_walmart_scan(query, page, RESULTS_PER_PAGE, sort_value, order_value, requested_by)

    tasks = [scan_one(query, page, sort_value, order_value) for query in preset_queries for sort_value, order_value in SORT_PASSES for page in range(1, PAGES_PER_QUERY + 1)]

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
    aggregate = ProviderScanResult(provider_key="walmart", candidates=tuple(deduped_candidates), warnings=tuple(warnings), page=1, page_size=len(all_candidates), start_index=1, has_next_page=True)
    verified_cards = deal_scanner.build_walmart_cards(aggregate, min_discount=starting_discount, alerts_only=False)
    verified_cards = rank_verified_cards(dedupe_cards(verified_cards))

    review_candidates = build_review_candidate_cards(list(deduped_candidates), limit=REVIEW_LEAD_LIMIT)
    scout_cards = scout_low_price_leads(deduped_candidates, limit=REVIEW_LEAD_LIMIT, search_query="")
    review_candidates = merge_review_and_scout_cards(review_candidates, scout_cards, limit=REVIEW_LEAD_LIMIT)
    review_candidates = ReviewCandidateResult(cards=rank_review_cards(review_candidates.cards), under_threshold_count=review_candidates.under_threshold_count, missing_reference_count=review_candidates.missing_reference_count, weak_reference_count=review_candidates.weak_reference_count, missing_current_count=review_candidates.missing_current_count, no_value_signal_count=review_candidates.no_value_signal_count, rejected_bad_value_count=review_candidates.rejected_bad_value_count, exact_match_count=getattr(review_candidates, "exact_match_count", 0))
    price_memory = None
    observed_price_memory = None
    cards = verified_cards
    if use_price_memory and db is not None and guild_id is not None:
        observed_price_memory = await select_observed_price_drop_cards(db, guild_id=guild_id, candidates=list(deduped_candidates), min_discount=starting_discount, limit=None)
        price_memory = await select_price_intelligent_cards(db, guild_id=guild_id, cards=verified_cards, fallback_retailer="walmart", limit=None)
        cards = rank_verified_cards(dedupe_cards([*price_memory.shown, *observed_price_memory.cards]))

    return VerifiedHuntResult(
        cards=cards,
        pages_checked=pages_checked,
        products_checked=len(all_candidates),
        warnings=warnings,
        searches_attempted=searches_attempted,
        min_discount=starting_discount,
        price_memory=price_memory,
        observed_price_memory=observed_price_memory,
        total_verified_cards=len(verified_cards),
        review_candidates=review_candidates,
        category_key=preset.key,
        route_stats=merged_route_stats,
        scout_lead_count=len(scout_cards),
        memory_recheck_count=len(memory_seeds),
    )


def install_verified_discount_hunt() -> None:
    if getattr(deal_scanner, "_sniperplug_verified_discount_hunt_installed", False):
        return
    deal_scanner.HUNT_PRESETS.clear()
    deal_scanner.HUNT_PRESETS.update(HUNT_PRESETS)
    deal_scanner.run_preset_hunt = run_verified_discount_hunt
    deal_scanner.build_hunt_menu_embed = build_verified_hunt_menu_embed
    deal_scanner.HuntPresetMenuView.__init__ = verified_menu_init
    deal_scanner.HuntPresetButton.callback = verified_hunt_button_callback
    deal_scanner.build_preset_hunt_summary = build_verified_hunt_summary
    deal_scanner._sniperplug_verified_discount_hunt_installed = True
