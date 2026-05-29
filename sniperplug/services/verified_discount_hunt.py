from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, merge_route_stats, tag_candidates_with_route, top_route_lines
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.low_price_scout import scout_low_price_leads
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.walmart_price_memory import PriceMemorySelection, select_price_intelligent_cards
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards


TRUE_DISCOUNT_MIN = 50
RESULTS_PER_PAGE = 25
PAGES_PER_QUERY = 5
SCAN_CONCURRENCY = 6
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
            "online clearance",
            "overstock clearance",
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
    "tech": ("Tech & Gaming", "🎮", "Electronics, gaming, TVs, monitors, phones, laptops, and restored tech.", ("electronics clearance", "electronics rollback", "gaming clearance", "laptop clearance", "tv clearance", "monitor clearance", "phone clearance", "prepaid phone clearance", "straight talk phone", "open box electronics", "restored electronics", "refurbished laptop")),
    "beauty": (
        "Beauty & Fragrance",
        "💄",
        "Perfume, cologne, designer fragrance, grooming, and beauty value leads.",
        (
            "beauty clearance",
            "fragrance",
            "fragrance clearance",
            "designer fragrance",
            "designer fragrance clearance",
            "designer cologne",
            "designer perfume",
            "cologne",
            "cologne clearance",
            "men cologne",
            "men cologne clearance",
            "perfume",
            "perfume clearance",
            "eau de parfum",
            "eau de parfum clearance",
            "eau de toilette",
            "eau de toilette clearance",
            "dolce gabbana cologne",
            "versace cologne",
            "gucci perfume",
            "ysl fragrance",
            "armani cologne",
            "calvin klein fragrance",
            "premium beauty clearance",
        ),
    ),
    "home": ("Home & Kitchen", "🏠", "Kitchen, home, furniture, patio, appliances, and storage.", ("home clearance", "home rollback", "kitchen clearance", "appliance clearance", "furniture clearance", "patio clearance", "vacuum clearance", "air fryer clearance", "coffee maker clearance", "mattress clearance")),
    "toys": ("Toys & Gifts", "🧸", "Toys, LEGO, games, collectibles, and giftable markdowns.", ("toy clearance", "toy rollback", "lego clearance", "pokemon cards", "board game clearance", "collectible clearance", "video game clearance", "barbie clearance")),
    "auto_tools": ("Auto & Tools", "🛠️", "Tools, garage, car care, oil, and DIY markdowns.", ("tool clearance", "tool rollback", "auto clearance", "drill clearance", "dewalt clearance", "milwaukee clearance", "hart tools clearance", "pressure washer clearance", "car care clearance", "motor oil rollback")),
    "essentials": ("Daily Essentials", "🧼", "Household, grocery, personal care, baby, pet, and coupon/cash value checks.", ("household clearance", "household rollback", "grocery clearance", "cleaning supplies clearance", "laundry detergent rollback", "paper goods rollback", "toilet paper rollback", "personal care clearance", "diaper clearance", "baby clearance", "pet clearance")),
}

# Backward-compatible broad route constant used by older tests/helpers.
DISCOVERY_QUERIES = CATEGORY_ROUTES["all"][3]

SORT_PASSES: tuple[tuple[str | None, str | None], ...] = (
    (None, None),
    ("price", "ascending"),
)

HUNT_PRESETS: dict[str, HuntPreset] = {
    key: HuntPreset(key, label, emoji, description, queries, TRUE_DISCOUNT_MIN)
    for key, (label, emoji, description, queries) in CATEGORY_ROUTES.items()
}
ALL_VERIFIED_PRESET = HuntPreset(
    ALL_VERIFIED_HUNT_KEY,
    CATEGORY_ROUTES["all"][0],
    CATEGORY_ROUTES["all"][1],
    CATEGORY_ROUTES["all"][2],
    DISCOVERY_QUERIES,
    TRUE_DISCOUNT_MIN,
)


@dataclass(frozen=True)
class VerifiedHuntResult:
    cards: list[DealCard]
    pages_checked: int
    products_checked: int
    warnings: list[str]
    searches_attempted: int
    min_discount: int = TRUE_DISCOUNT_MIN
    price_memory: PriceMemorySelection | None = None
    total_verified_cards: int = 0
    review_candidates: ReviewCandidateResult | None = None
    category_key: str = "all"
    route_stats: tuple[SearchRouteStats, ...] = ()
    scout_lead_count: int = 0


async def run_verified_discount_hunt(preset: HuntPreset | None = None, requested_by: str = "") -> tuple[list[DealCard], int, int, list[str], int]:
    result = await collect_verified_discount_cards(preset=preset or ALL_VERIFIED_PRESET, requested_by=requested_by)
    return result.cards, result.pages_checked, result.products_checked, result.warnings, result.min_discount


async def collect_verified_discount_cards(*, requested_by: str, preset: HuntPreset | None = None, db=None, guild_id: int | None = None, use_price_memory: bool = False) -> VerifiedHuntResult:
    preset = preset or ALL_VERIFIED_PRESET
    warnings: list[str] = []
    all_candidates: list[SourceCandidate] = []
    route_stats: list[SearchRouteStats] = []
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def scan_one(query: str, page: int, sort_value: str | None, order_value: str | None) -> tuple[str, ProviderScanResult]:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            return query, await deal_scanner.run_walmart_scan(query, page, RESULTS_PER_PAGE, sort_value, order_value, requested_by)

    tasks = [
        scan_one(query, page, sort_value, order_value)
        for query in preset.queries
        for sort_value, order_value in SORT_PASSES
        for page in range(1, PAGES_PER_QUERY + 1)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        pages_checked += 1
        if isinstance(item, Exception):
            text = str(item) or item.__class__.__name__
            if text not in warnings:
                warnings.append(text)
            route_stats.append(SearchRouteStats(query="unknown", pages_checked=1, returned_products=0, warnings=(text,)))
            continue
        query, result = item
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
    verified_cards = deal_scanner.build_walmart_cards(aggregate, min_discount=TRUE_DISCOUNT_MIN, alerts_only=False)
    verified_cards = rank_verified_cards(dedupe_cards(verified_cards))

    review_candidates = build_review_candidate_cards(list(deduped_candidates))
    scout_cards = scout_low_price_leads(deduped_candidates, limit=12, search_query="")
    review_candidates = merge_review_and_scout_cards(review_candidates, scout_cards, limit=12)
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
    price_memory = None
    cards = verified_cards
    if use_price_memory and db is not None and guild_id is not None:
        price_memory = await select_price_intelligent_cards(db, guild_id=guild_id, cards=verified_cards, fallback_retailer="walmart", limit=None)
        cards = rank_verified_cards(price_memory.shown)

    return VerifiedHuntResult(
        cards=cards,
        pages_checked=pages_checked,
        products_checked=len(all_candidates),
        warnings=warnings,
        searches_attempted=searches_attempted,
        min_discount=TRUE_DISCOUNT_MIN,
        price_memory=price_memory,
        total_verified_cards=len(verified_cards),
        review_candidates=review_candidates,
        category_key=preset.key,
        route_stats=merged_route_stats,
        scout_lead_count=len(scout_cards),
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


def verified_menu_init(self) -> None:
    discord.ui.View.__init__(self, timeout=300)
    for index, preset in enumerate(HUNT_PRESETS.values()):
        self.add_item(deal_scanner.HuntPresetButton(preset, row=index // 2))


async def verified_hunt_button_callback(self, interaction: discord.Interaction) -> None:
    preset = self.preset
    lock_key = ScanLockKey(
        guild_id=interaction.guild_id,
        user_id=interaction.user.id,
        action="verified_discount_hunt",
        preset=preset.key,
        min_discount=TRUE_DISCOUNT_MIN,
    )
    if not await deal_scanner.acquire_scan_lock(
        interaction,
        lock_key,
        self.view,
        f"⏳ Running Walmart {preset.label} 50%+ hunt. Buttons are locked so this cannot double-post...",
    ):
        return
    try:
        health_error = await deal_scanner.provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return
        from sniperplug.services.deal_finder_engine import find_walmart_deals_for_preset

        result = await find_walmart_deals_for_preset(
            requested_by=str(interaction.user.id),
            preset=preset,
            db=getattr(interaction.client, "db", None),
            guild_id=interaction.guild_id,
            use_price_memory=True,
        )
        summary = build_verified_hunt_result_embed(result)
        public_result = await maybe_post_public_deal_cards(
            bot=interaction.client,
            guild_id=interaction.guild_id,
            cards=result.cards,
            source_label=f"hunt:{preset.key}:verified_50_plus",
            fallback_retailer="walmart",
        )
        deal_scanner.add_public_posting_field(summary, public_result)
        await send_card_batches(interaction, summary=summary, cards=result.cards, review_cards=result.review_candidates.cards if result.review_candidates else [])
    finally:
        await scan_operation_locks.release(lock_key)


def build_verified_hunt_menu_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🚨 SniperPlug Walmart Hunt",
        description=(
            "Pick a category. Each button scans Walmart sale/result surfaces for **API-verified 50%+ deals**.\n"
            "Categories now include broad department seeds plus private low-price scout leads, so useful products are not hidden just because Walmart omitted was/typical markdown proof."
        ),
        color=discord.Color.red(),
    )
    for preset in HUNT_PRESETS.values():
        embed.add_field(name=f"{preset.emoji} {preset.label}", value=f"{preset.description}\nStarts at **50%+ verified**. Scout leads are private.", inline=False)
    embed.set_footer(text=f"Each category checks {len(SORT_PASSES)} sort passes × up to {PAGES_PER_QUERY} pages per route. Math must come from trusted API product prices.")
    return embed


def build_verified_hunt_summary(preset: HuntPreset, pages_checked: int, products_checked: int, found_count: int, warnings: tuple[str, ...], shown_discount: int) -> discord.Embed:
    result = VerifiedHuntResult(cards=[], pages_checked=pages_checked, products_checked=products_checked, warnings=list(warnings), searches_attempted=pages_checked, min_discount=TRUE_DISCOUNT_MIN, total_verified_cards=found_count, category_key=preset.key)
    return build_verified_hunt_result_embed(result)


def build_verified_hunt_result_embed(result: VerifiedHuntResult) -> discord.Embed:
    found_total = result.total_verified_cards if result.total_verified_cards else len(result.cards)
    review_count = len(result.review_candidates.cards) if result.review_candidates else 0
    preset = HUNT_PRESETS.get(result.category_key, ALL_VERIFIED_PRESET)
    embed = discord.Embed(
        title=f"{preset.emoji} {preset.label} Hunt Results",
        description=(
            f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} API pages**\n"
            f"Routes: **{len(preset.queries)}** • Sort passes: **{len(SORT_PASSES)}** • Page size: **{RESULTS_PER_PAGE}**\n"
            f"Verified 50%+ total: **{found_total}** • Shown now: **{len(result.cards)}**\n"
            f"Review/flip/scout candidates: **{review_count}**"
        ),
        color=discord.Color.red() if result.cards else discord.Color.dark_gold(),
    )
    route_lines = top_route_lines(result.route_stats, limit=5)
    if route_lines:
        embed.add_field(name="🧭 Productive routes", value="\n".join(route_lines), inline=False)
    if result.price_memory is not None:
        embed.add_field(name="🧠 Price memory", value=result.price_memory.summary_line(), inline=False)
    if result.review_candidates is not None:
        embed.add_field(name="🟨 Review / flip / scout audit", value=result.review_candidates.summary_line(), inline=False)
    if result.scout_lead_count:
        embed.add_field(name="🔎 Low-price scout", value=f"Surfaced **{result.scout_lead_count}** private scout lead(s) from broad category scans.", inline=False)
    if not result.cards and review_count:
        embed.add_field(
            name="No auto-postable verified 50%+ deals — showing review/flip/scout candidates",
            value="Private candidates can be manually checked and posted. They are not auto-posted unless trusted Walmart price math passes.",
            inline=False,
        )
    elif not result.cards:
        embed.add_field(
            name="No verified 50%+ API markdowns found",
            value="Walmart did not return trusted 50%+ markdown proof, but broad route/scout coverage still checked category value leads.",
            inline=False,
        )
    if result.warnings:
        embed.add_field(name="⚠️ API notes", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
    embed.set_footer(text="Verified cards can public-post. Review/flip/scout leads are private and require manual checkout/comp checks.")
    return embed


async def send_card_batches(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard], review_cards: list[DealCard] | None = None) -> None:
    await interaction.followup.send(embed=summary, ephemeral=True)
    for batch in chunked(cards, 5):
        await interaction.followup.send(embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)
    for batch in chunked(review_cards or [], 5):
        await interaction.followup.send(content="🟨 Review/flip/scout API leads — private only, not public-posted as verified deals.", embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)


def merge_review_and_scout_cards(review: ReviewCandidateResult, scout_cards: list[DealCard], *, limit: int = 12) -> ReviewCandidateResult:
    merged: list[DealCard] = []
    seen: set[str] = set()
    for card in [*review.cards, *scout_cards]:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url or card.label
        price = getattr(card, "current_price", None)
        identity = f"{key}:price:{price}"
        if identity in seen:
            continue
        seen.add(identity)
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
    )


def dedupe_cards(cards: list[DealCard]) -> list[DealCard]:
    seen: set[str] = set()
    unique: list[DealCard] = []
    for card in cards:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url or card.label
        price = getattr(card, "current_price", None)
        identity = f"{key}:price:{price}"
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(card)
    return unique


def chunked(cards: list[DealCard], size: int) -> list[list[DealCard]]:
    return [cards[index : index + size] for index in range(0, len(cards), size)]
