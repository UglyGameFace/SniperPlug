from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks


TRUE_DISCOUNT_MIN = 50
RESULTS_PER_PAGE = 25
PAGES_PER_QUERY = 5
SCAN_CONCURRENCY = 8
ALL_VERIFIED_HUNT_KEY = "all_verified_discounts"

# These are not user-facing presets. They are broad Walmart API discovery routes
# used to pull verified markdown candidates from multiple sale/result surfaces.
DISCOVERY_QUERIES: tuple[str, ...] = (
    "clearance",
    "rollback",
    "price drop",
    "reduced price",
    "special buy",
    "overstock",
    "closeout",
    "electronics clearance",
    "gaming clearance",
    "laptop clearance",
    "tv clearance",
    "monitor clearance",
    "toy clearance",
    "lego clearance",
    "home clearance",
    "kitchen clearance",
    "patio clearance",
    "furniture clearance",
    "vacuum clearance",
    "tool clearance",
    "auto clearance",
    "appliance clearance",
    "seasonal clearance",
    "open box",
    "restored",
    "refurbished",
    "like new",
)

SORT_PASSES: tuple[tuple[str | None, str | None], ...] = (
    (None, None),
    ("price", "ascending"),
    ("bestseller", None),
    ("new", None),
)

ALL_VERIFIED_PRESET = HuntPreset(
    ALL_VERIFIED_HUNT_KEY,
    "All Verified 50%+ Deals",
    "🚨",
    "Scans Walmart broadly for API-verified 50%+ markdowns and price-glitch candidates.",
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


async def run_verified_discount_hunt(preset: HuntPreset | None = None, requested_by: str = "") -> tuple[list[DealCard], int, int, list[str], int]:
    result = await collect_verified_discount_cards(requested_by=requested_by)
    return result.cards, result.pages_checked, result.products_checked, result.warnings, result.min_discount


async def collect_verified_discount_cards(*, requested_by: str) -> VerifiedHuntResult:
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
        for query in DISCOVERY_QUERIES
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

    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(deal_scanner.dedupe_candidates(all_candidates)),
        warnings=tuple(warnings),
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=True,
    )
    cards = deal_scanner.build_walmart_cards(aggregate, min_discount=TRUE_DISCOUNT_MIN, alerts_only=False)
    cards = dedupe_cards(cards)
    cards.sort(key=lambda card: (float(getattr(card, "discount", 0.0) or 0.0), -(float(getattr(card, "current_price", 0.0) or 0.0))), reverse=True)
    return VerifiedHuntResult(
        cards=cards,
        pages_checked=pages_checked,
        products_checked=len(all_candidates),
        warnings=warnings,
        searches_attempted=searches_attempted,
        min_discount=TRUE_DISCOUNT_MIN,
    )


def install_verified_discount_hunt() -> None:
    """Make /hunt a global verified-discount hunt, not a category preset menu."""
    if getattr(deal_scanner, "_sniperplug_verified_discount_hunt_installed", False):
        return
    deal_scanner.HUNT_PRESETS.clear()
    deal_scanner.HUNT_PRESETS[ALL_VERIFIED_HUNT_KEY] = ALL_VERIFIED_PRESET
    deal_scanner.run_preset_hunt = run_verified_discount_hunt
    deal_scanner.build_hunt_menu_embed = build_verified_hunt_menu_embed
    deal_scanner.HuntPresetMenuView.__init__ = verified_menu_init
    deal_scanner.HuntPresetButton.callback = verified_hunt_button_callback
    deal_scanner.build_preset_hunt_summary = build_verified_hunt_summary
    deal_scanner._sniperplug_verified_discount_hunt_installed = True


def verified_menu_init(self) -> None:
    discord.ui.View.__init__(self, timeout=300)
    self.add_item(deal_scanner.HuntPresetButton(ALL_VERIFIED_PRESET, row=0))


async def verified_hunt_button_callback(self, interaction: discord.Interaction) -> None:
    lock_key = ScanLockKey(
        guild_id=interaction.guild_id,
        user_id=interaction.user.id,
        action="verified_discount_hunt",
        preset=ALL_VERIFIED_HUNT_KEY,
        min_discount=TRUE_DISCOUNT_MIN,
    )
    if not await deal_scanner.acquire_scan_lock(
        interaction,
        lock_key,
        self.view,
        "⏳ Running Walmart verified 50%+ hunt. Buttons are locked so this cannot double-post...",
    ):
        return
    try:
        health_error = await deal_scanner.provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return
        result = await collect_verified_discount_cards(requested_by=str(interaction.user.id))
        summary = build_verified_hunt_result_embed(result)
        public_result = await maybe_post_public_deal_cards(
            bot=interaction.client,
            guild_id=interaction.guild_id,
            cards=result.cards,
            source_label="hunt:verified_50_plus",
            fallback_retailer="walmart",
        )
        deal_scanner.add_public_posting_field(summary, public_result)
        await send_card_batches(interaction, summary=summary, cards=result.cards)
    finally:
        await scan_operation_locks.release(lock_key)


def build_verified_hunt_menu_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🚨 SniperPlug Verified Walmart Hunt",
        description=(
            "Tap the button to scan Walmart broadly for **API-verified 50%+ discounts**.\n"
            "No category presets. No relaxed 20%-30% filler. No guessed discount math."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(
        name="What counts",
        value=(
            "• Current price must come from Walmart API\n"
            "• Was/typical reference must be trusted by the Walmart proof rules\n"
            "• Savings must calculate to **50%+ off**"
        ),
        inline=False,
    )
    embed.add_field(
        name="What gets scanned",
        value=f"{len(DISCOVERY_QUERIES)} broad discovery routes × {len(SORT_PASSES)} sort passes × up to {PAGES_PER_QUERY} pages.",
        inline=False,
    )
    embed.set_footer(text="Manual hunt posts/caches through the normal duplicate guard; lower prices can appear again.")
    return embed


def build_verified_hunt_summary(preset: HuntPreset, pages_checked: int, products_checked: int, found_count: int, warnings: tuple[str, ...], shown_discount: int) -> discord.Embed:
    result = VerifiedHuntResult(cards=[], pages_checked=pages_checked, products_checked=products_checked, warnings=list(warnings), searches_attempted=pages_checked, min_discount=TRUE_DISCOUNT_MIN)
    embed = build_verified_hunt_result_embed(result)
    embed.description = (embed.description or "") + f"\nFound: **{found_count} verified card(s)**"
    return embed


def build_verified_hunt_result_embed(result: VerifiedHuntResult) -> discord.Embed:
    embed = discord.Embed(
        title="🚨 Verified 50%+ Walmart Hunt Results",
        description=(
            f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} API result pages**\n"
            f"Discovery routes: **{len(DISCOVERY_QUERIES)}** • Sort passes: **{len(SORT_PASSES)}** • Page size: **{RESULTS_PER_PAGE}**\n"
            f"Minimum discount: **{result.min_discount}%+ verified by Walmart API fields**\n"
            f"Found: **{len(result.cards)} verified card(s)**"
        ),
        color=discord.Color.red() if result.cards else discord.Color.dark_gold(),
    )
    if not result.cards:
        embed.add_field(
            name="No verified 50%+ API markdowns found",
            value="SniperPlug did not lower the threshold or show filler. Try again later when Walmart returns stronger markdown proof.",
            inline=False,
        )
    if result.warnings:
        embed.add_field(name="⚠️ API notes", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
    embed.set_footer(text="Only API-verified 50%+ markdown cards are shown. Prices can still revert at checkout.")
    return embed


async def send_card_batches(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard]) -> None:
    await interaction.followup.send(embed=summary, ephemeral=True)
    for batch in chunked(cards, 5):
        await interaction.followup.send(embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)


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
