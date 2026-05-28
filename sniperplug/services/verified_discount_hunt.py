from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.walmart_price_memory import PriceMemorySelection, select_price_intelligent_cards
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards


TRUE_DISCOUNT_MIN = 50
RESULTS_PER_PAGE = 25
PAGES_PER_QUERY = 3
SCAN_CONCURRENCY = 6
ALL_VERIFIED_HUNT_KEY = "all_verified_discounts"

CATEGORY_ROUTES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "all": ("All Walmart", "🚨", "All major Walmart sale surfaces, capped for speed.", ("clearance", "rollback", "price drop", "reduced price", "special buy", "electronics clearance", "toy clearance", "home clearance", "kitchen clearance", "tool clearance", "auto clearance", "open box", "restored")),
    "tech": ("Tech & Gaming", "🎮", "Electronics, gaming, TVs, monitors, laptops, and restored tech.", ("electronics clearance", "gaming clearance", "laptop clearance", "tv clearance", "monitor clearance", "open box electronics", "restored monitor", "refurbished laptop")),
    "home": ("Home & Kitchen", "🏠", "Kitchen, home, furniture, patio, appliances, and storage.", ("home clearance", "kitchen clearance", "appliance clearance", "furniture clearance", "patio clearance", "vacuum clearance")),
    "toys": ("Toys & Gifts", "🧸", "Toys, LEGO, games, collectibles, and giftable markdowns.", ("toy clearance", "lego clearance", "board game clearance", "collectible clearance", "video game clearance")),
    "auto_tools": ("Auto & Tools", "🛠️", "Tools, garage, car care, oil, and DIY markdowns.", ("tool clearance", "auto clearance", "drill clearance", "pressure washer clearance", "car care clearance")),
    "essentials": ("Daily Essentials", "🧼", "Household, grocery, personal care, and coupon/cash value checks.", ("household clearance", "grocery clearance", "cleaning supplies clearance", "laundry detergent rollback", "paper goods rollback")),
}

SORT_PASSES: tuple[tuple[str | None, str | None], ...] = (
    (None, None),
    ("price", "ascending"),
)

HUNT_PRESETS: dict[str, HuntPreset] = {
    key: HuntPreset(key, label, emoji, description, queries, TRUE_DISCOUNT_MIN)
    for key, (label, emoji, description, queries) in CATEGORY_ROUTES.items()
}
ALL_VERIFIED_PRESET = HUNT_PRESETS["all"]


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


async def run_verified_discount_hunt(preset: HuntPreset | None = None, requested_by: str = "") -> tuple[list[DealCard], int, int, list[str], int]:
    result = await collect_verified_discount_cards(preset=preset or ALL_VERIFIED_PRESET, requested_by=requested_by)
    return result.cards, result.pages_checked, result.products_checked, result.warnings, result.min_discount


async def collect_verified_discount_cards(*, requested_by: str, preset: HuntPreset | None = None, db=None, guild_id: int | None = None, use_price_memory: bool = False) -> VerifiedHuntResult:
    preset = preset or ALL_VERIFIED_PRESET
    warnings: list[str] = []
    all_candidates: list[SourceCandidate] = []
    pages_checked = 0
    searches_attempted = 0
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def scan_one(query: str, page: int, sort_value: str | None, order_value: str | None) -> ProviderScanResult:
        nonlocal searches_attempted
        async with semaphore:
            searches_attempted += 1
            return await deal_scanner.run_walmart_scan(query, page, RESULTS_PER_PAGE, sort_value, order_value, requested_by)

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
            continue
        all_candidates.extend(item.candidates)
        warnings.extend(w for w in item.warnings if w not in warnings)

    deduped_candidates = deal_scanner.dedupe_candidates(all_candidates)
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
    review_candidates = ReviewCandidateResult(
        cards=rank_review_cards(review_candidates.cards),
        under_threshold_count=review_candidates.under_threshold_count,
        missing_reference_count=review_candidates.missing_reference_count,
        weak_reference_count=review_candidates.weak_reference_count,
        missing_current_count=review_candidates.missing_current_count,
        no_value_signal_count=review_candidates.no_value_signal_count,
        rejected_bad_value_count=review_candidates.rejected_bad_value_count,
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
            "Categories are broad route groups, not tiny preset product searches. Review-only cards are private and never auto-post."
        ),
        color=discord.Color.red(),
    )
    for preset in HUNT_PRESETS.values():
        embed.add_field(name=f"{preset.emoji} {preset.label}", value=f"{preset.description}\nStarts at **50%+ verified**.", inline=False)
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
            f"Review/flip candidates: **{review_count}**"
        ),
        color=discord.Color.red() if result.cards else discord.Color.dark_gold(),
    )
    if result.price_memory is not None:
        embed.add_field(name="🧠 Price memory", value=result.price_memory.summary_line(), inline=False)
    if result.review_candidates is not None:
        embed.add_field(name="🟨 Review / flip audit", value=result.review_candidates.summary_line(), inline=False)
    if not result.cards and review_count:
        embed.add_field(
            name="No auto-postable verified 50%+ deals — showing review/flip candidates",
            value="Review candidates are private only. They are API-backed leads, but not verified deals because trusted 50% markdown math did not pass.",
            inline=False,
        )
    elif not result.cards:
        embed.add_field(
            name="No verified 50%+ API markdowns found",
            value="Walmart did not return trusted 50%+ markdown proof or review-worthy value signals in this category run.",
            inline=False,
        )
    if result.warnings:
        embed.add_field(name="⚠️ API notes", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
    embed.set_footer(text="Verified cards can public-post. Review/flip leads are private and require manual checkout/comp checks.")
    return embed


async def send_card_batches(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard], review_cards: list[DealCard] | None = None) -> None:
    await interaction.followup.send(embed=summary, ephemeral=True)
    for batch in chunked(cards, 5):
        await interaction.followup.send(embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)
    for batch in chunked(review_cards or [], 5):
        await interaction.followup.send(content="🟨 Review/flip API leads — private only, not public-posted as verified deals.", embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)


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
