from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.providers.base import ProviderScanRequest, ProviderScanResult, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.routing import route_label


SORT_CHOICES = [
    app_commands.Choice(name="Relevance", value="relevance"),
    app_commands.Choice(name="Price: low to high", value="price_ascending"),
    app_commands.Choice(name="Price: high to low", value="price_descending"),
    app_commands.Choice(name="Bestseller", value="bestseller"),
    app_commands.Choice(name="Customer rating", value="customerRating"),
    app_commands.Choice(name="New", value="new"),
]


@dataclass(frozen=True)
class HuntPreset:
    key: str
    label: str
    emoji: str
    description: str
    queries: tuple[str, ...]
    min_discount: int = 50


HUNT_PRESETS: dict[str, HuntPreset] = {
    "electronics": HuntPreset(
        key="electronics",
        label="Electronics",
        emoji="🔥",
        description="Monitors, TVs, headphones, storage, gaming gear.",
        queries=("gaming monitor", "4k tv", "headphones", "ssd", "gaming keyboard"),
        min_discount=45,
    ),
    "home": HuntPreset(
        key="home",
        label="Home",
        emoji="🏠",
        description="Appliances, furniture, kitchen, patio, home upgrades.",
        queries=("air fryer", "patio furniture", "vacuum", "coffee maker", "storage cabinet"),
        min_discount=50,
    ),
    "household": HuntPreset(
        key="household",
        label="Household",
        emoji="🧼",
        description="Detergent, cleaning, paper goods, daily-use items.",
        queries=("laundry detergent", "paper towels", "cleaning supplies", "trash bags", "dish soap"),
        min_discount=35,
    ),
    "toys": HuntPreset(
        key="toys",
        label="Toys",
        emoji="🧸",
        description="LEGO, games, outdoor toys, collectibles, kid gifts.",
        queries=("lego", "toys clearance", "board game", "pokemon", "outdoor toy"),
        min_discount=45,
    ),
    "auto": HuntPreset(
        key="auto",
        label="Auto",
        emoji="🚗",
        description="Oil, car care, tools, parts, accessories.",
        queries=("motor oil", "car battery", "tire inflator", "car wash", "socket set"),
        min_discount=40,
    ),
    "beauty": HuntPreset(
        key="beauty",
        label="Beauty",
        emoji="💄",
        description="Personal care, grooming, skin care, hair tools.",
        queries=("electric toothbrush", "razor", "skin care", "hair dryer", "shampoo"),
        min_discount=40,
    ),
}


class DealScannerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hunt", description="Tap a category and let SniperPlug hunt deals for you.")
    async def hunt(self, interaction: discord.Interaction) -> None:
        embed = build_hunt_menu_embed()
        await interaction.response.send_message(embed=embed, view=HuntPresetMenuView(), ephemeral=True)

    @app_commands.command(name="deals", description="Find deals the easy way. Just type what you want.")
    @app_commands.describe(search="What are you shopping for? Example: monitor, lego, tide, patio set, air fryer.")
    async def deals(self, interaction: discord.Interaction, search: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._send_walmart_scan(
            interaction=interaction,
            query=search,
            min_discount=50,
            page=1,
            max_results=10,
            sort_value=None,
            order_value=None,
            alerts_only=False,
            simple_mode=True,
        )

    @app_commands.command(name="walmart_scan", description="Advanced Walmart deal scan for staff/admins.")
    @app_commands.describe(
        query="Product search, like gaming monitor, tide detergent, lego, patio set.",
        min_discount="Only show deals at or above this percent off. Try 50 or 80.",
        page="Walmart result page to scan. Use page 2/3 if page 1 repeats weak deals.",
        max_results="How many Walmart results to inspect on this page. Max 25.",
        sort="Optional Walmart sorting mode.",
        alerts_only="Only show candidates SniperPlug would alert on.",
    )
    @app_commands.choices(sort=SORT_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def walmart_scan(
        self,
        interaction: discord.Interaction,
        query: str,
        min_discount: app_commands.Range[int, 0, 95] = 30,
        page: app_commands.Range[int, 1, 40] = 1,
        max_results: app_commands.Range[int, 1, 25] = 10,
        sort: app_commands.Choice[str] | None = None,
        alerts_only: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        sort_value, order_value = parse_sort_choice(sort.value if sort else None)
        await self._send_walmart_scan(
            interaction=interaction,
            query=query,
            min_discount=min_discount,
            page=page,
            max_results=max_results,
            sort_value=sort_value,
            order_value=order_value,
            alerts_only=alerts_only,
            simple_mode=False,
        )

    async def _send_walmart_scan(
        self,
        interaction: discord.Interaction,
        query: str,
        min_discount: int,
        page: int,
        max_results: int,
        sort_value: str | None,
        order_value: str | None,
        alerts_only: bool,
        simple_mode: bool,
    ) -> None:
        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("Walmart search is not connected yet.", ephemeral=True)
            return

        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(
                "Deal search is not ready yet. Staff needs to finish the Walmart connection first.",
                ephemeral=True,
            )
            return

        result = await run_walmart_scan(
            query=query,
            page=page,
            max_results=max_results,
            sort_value=sort_value,
            order_value=order_value,
            requested_by=str(interaction.user.id),
        )
        cards = build_walmart_cards(result, min_discount=min_discount, alerts_only=alerts_only)
        summary = build_scan_summary(
            result,
            query=query,
            min_discount=min_discount,
            alerts_only=alerts_only,
            simple_mode=simple_mode,
        )

        if not cards:
            summary.add_field(
                name="No strong matches here",
                value=no_match_help(query=query, min_discount=min_discount, page=page, simple_mode=simple_mode),
                inline=False,
            )
            view = DealSearchControlView(
                query=query,
                page=page,
                min_discount=min_discount,
                max_results=max_results,
                sort_value=sort_value,
                order_value=order_value,
                alerts_only=alerts_only,
                simple_mode=simple_mode,
            )
            await interaction.followup.send(embed=summary, view=view, ephemeral=True)
            return

        embeds = [summary] + [card.embed for card in cards[:5]]
        view = DealSearchControlView(
            query=query,
            page=page,
            min_discount=min_discount,
            max_results=max_results,
            sort_value=sort_value,
            order_value=order_value,
            alerts_only=alerts_only,
            simple_mode=simple_mode,
            cards=cards[:5],
            has_next_page=result.has_next_page,
        )
        await interaction.followup.send(embeds=embeds, view=view, ephemeral=True)

    @hunt.error
    async def hunt_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = f"Deal hunt hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @deals.error
    async def deals_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = f"Deal search hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @walmart_scan.error
    async def walmart_scan_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to run advanced Walmart scans. Use `/deals` for the simple search."
        else:
            message = f"Walmart scan hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class DealCard:
    def __init__(self, embed: discord.Embed, url: str, label: str, score: int = 0, discount: float = 0.0):
        self.embed = embed
        self.url = url
        self.label = label
        self.score = score
        self.discount = discount


class HuntPresetMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(HuntPresetButton(HUNT_PRESETS["electronics"], row=0))
        self.add_item(HuntPresetButton(HUNT_PRESETS["home"], row=0))
        self.add_item(HuntPresetButton(HUNT_PRESETS["household"], row=0))
        self.add_item(HuntPresetButton(HUNT_PRESETS["toys"], row=1))
        self.add_item(HuntPresetButton(HUNT_PRESETS["auto"], row=1))
        self.add_item(HuntPresetButton(HUNT_PRESETS["beauty"], row=1))


class HuntPresetButton(discord.ui.Button):
    def __init__(self, preset: HuntPreset, row: int):
        super().__init__(
            label=preset.label,
            emoji=preset.emoji,
            style=discord.ButtonStyle.primary,
            row=row,
            custom_id=f"hunt_preset:{preset.key}",
        )
        self.preset = preset

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        health_error = await provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return

        cards, pages_checked, products_checked, warnings = await run_preset_hunt(
            preset=self.preset,
            requested_by=str(interaction.user.id),
        )
        summary = build_preset_hunt_summary(
            preset=self.preset,
            pages_checked=pages_checked,
            products_checked=products_checked,
            found_count=len(cards),
            warnings=tuple(warnings),
        )
        if not cards:
            summary.add_field(
                name="No strong hits yet",
                value=(
                    "I checked several beginner-friendly searches and did not find a strong enough markdown.\n"
                    "Try another category, or use `/deals search:` with a specific item like `oled tv`, `lego`, `detergent`, or `ssd`."
                ),
                inline=False,
            )
            await interaction.followup.send(embed=summary, view=HuntPresetMenuView(), ephemeral=True)
            return

        embeds = [summary] + [card.embed for card in cards[:5]]
        await interaction.followup.send(embeds=embeds, view=PresetResultView(cards[:5]), ephemeral=True)


class PresetResultView(discord.ui.View):
    def __init__(self, cards: list[DealCard]):
        super().__init__(timeout=300)
        for idx, card in enumerate(cards, start=1):
            self.add_item(discord.ui.Button(label=f"View Deal {idx}", style=discord.ButtonStyle.link, url=card.url))
        self.add_item(discord.ui.Button(label="Run /hunt again for more categories", style=discord.ButtonStyle.secondary, disabled=True, row=1))


class DealSearchControlView(discord.ui.View):
    def __init__(
        self,
        query: str,
        page: int,
        min_discount: int,
        max_results: int,
        sort_value: str | None,
        order_value: str | None,
        alerts_only: bool,
        simple_mode: bool,
        cards: list[DealCard] | None = None,
        has_next_page: bool = False,
    ):
        super().__init__(timeout=300)
        self.query = query
        self.page = page
        self.min_discount = min_discount
        self.max_results = max_results
        self.sort_value = sort_value
        self.order_value = order_value
        self.alerts_only = alerts_only
        self.simple_mode = simple_mode
        self.has_next_page = has_next_page

        for idx, card in enumerate(cards or [], start=1):
            self.add_item(discord.ui.Button(label=f"View Deal {idx}", style=discord.ButtonStyle.link, url=card.url))

    @discord.ui.button(label="Next Page", emoji="➡️", style=discord.ButtonStyle.primary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._rerun(interaction, page=self.page + 1, min_discount=self.min_discount)

    @discord.ui.button(label="Hunt 80%+", emoji="🔥", style=discord.ButtonStyle.secondary, row=1)
    async def huge_discounts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._hunt_pages(interaction, min_discount=80, max_pages=5)

    @discord.ui.button(label="Show More", emoji="🔎", style=discord.ButtonStyle.secondary, row=1)
    async def show_more(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._rerun(interaction, page=self.page, min_discount=max(0, self.min_discount - 20))

    async def _rerun(self, interaction: discord.Interaction, page: int, min_discount: int) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await run_walmart_scan(
            query=self.query,
            page=page,
            max_results=self.max_results,
            sort_value=self.sort_value,
            order_value=self.order_value,
            requested_by=str(interaction.user.id),
        )
        cards = build_walmart_cards(result, min_discount=min_discount, alerts_only=self.alerts_only)
        summary = build_scan_summary(
            result,
            query=self.query,
            min_discount=min_discount,
            alerts_only=self.alerts_only,
            simple_mode=self.simple_mode,
        )
        if not cards:
            summary.add_field(
                name="No strong matches here",
                value=no_match_help(query=self.query, min_discount=min_discount, page=page, simple_mode=self.simple_mode),
                inline=False,
            )
            await interaction.followup.send(embed=summary, view=self._copy_for(page, min_discount, [], result.has_next_page), ephemeral=True)
            return

        embeds = [summary] + [card.embed for card in cards[:5]]
        await interaction.followup.send(
            embeds=embeds,
            view=self._copy_for(page, min_discount, cards[:5], result.has_next_page),
            ephemeral=True,
        )

    async def _hunt_pages(self, interaction: discord.Interaction, min_discount: int, max_pages: int) -> None:
        await interaction.response.defer(ephemeral=True)
        all_candidates: list[SourceCandidate] = []
        warnings: list[str] = []
        pages_checked = 0
        total_results: int | None = None
        has_next_page = False

        start_page = 1
        for page in range(start_page, start_page + max_pages):
            result = await run_walmart_scan(
                query=self.query,
                page=page,
                max_results=25,
                sort_value=self.sort_value,
                order_value=self.order_value,
                requested_by=str(interaction.user.id),
            )
            pages_checked += 1
            all_candidates.extend(result.candidates)
            warnings.extend(w for w in result.warnings if w not in warnings)
            total_results = result.total_results if result.total_results is not None else total_results
            has_next_page = result.has_next_page

            cards = build_walmart_cards(
                ProviderScanResult(
                    provider_key=result.provider_key,
                    candidates=tuple(all_candidates),
                    warnings=tuple(warnings),
                    total_results=total_results,
                    page=page,
                    page_size=25,
                    start_index=1,
                    has_next_page=has_next_page,
                ),
                min_discount=min_discount,
                alerts_only=self.alerts_only,
            )
            if cards:
                summary = build_hunt_summary(
                    query=self.query,
                    min_discount=min_discount,
                    pages_checked=pages_checked,
                    candidates_checked=len(all_candidates),
                    total_results=total_results,
                    found_count=len(cards),
                    warnings=tuple(warnings),
                    simple_mode=self.simple_mode,
                )
                embeds = [summary] + [card.embed for card in cards[:5]]
                await interaction.followup.send(
                    embeds=embeds,
                    view=self._copy_for(page, min_discount, cards[:5], has_next_page),
                    ephemeral=True,
                )
                return

            if not result.has_next_page:
                break

        summary = build_hunt_summary(
            query=self.query,
            min_discount=min_discount,
            pages_checked=pages_checked,
            candidates_checked=len(all_candidates),
            total_results=total_results,
            found_count=0,
            warnings=tuple(warnings),
            simple_mode=self.simple_mode,
        )
        summary.add_field(
            name="No 80%+ finds yet",
            value=(
                f"I checked **{len(all_candidates)} products across {pages_checked} page(s)** and did not find a true {min_discount}%+ markdown.\n"
                "Try **Show More** for normal deals, **Next Page** to keep going manually, or search a tighter term like `iphone case`, `iphone charger`, `oled tv`, `clearance toy`."
            ),
            inline=False,
        )
        await interaction.followup.send(embed=summary, view=self._copy_for(self.page, min_discount, [], has_next_page), ephemeral=True)

    def _copy_for(self, page: int, min_discount: int, cards: list[DealCard], has_next_page: bool) -> DealSearchControlView:
        return DealSearchControlView(
            query=self.query,
            page=page,
            min_discount=min_discount,
            max_results=self.max_results,
            sort_value=self.sort_value,
            order_value=self.order_value,
            alerts_only=self.alerts_only,
            simple_mode=self.simple_mode,
            cards=cards,
            has_next_page=has_next_page,
        )


async def provider_health_error_message() -> str | None:
    provider = provider_registry.get("walmart")
    if provider is None:
        return "Walmart search is not connected yet."
    health = await provider.healthcheck()
    if health.status != ProviderStatus.READY:
        return "Deal search is not ready yet. Staff needs to finish the Walmart connection first."
    return None


async def run_walmart_scan(
    query: str,
    page: int,
    max_results: int,
    sort_value: str | None,
    order_value: str | None,
    requested_by: str,
) -> ProviderScanResult:
    provider = provider_registry.get("walmart")
    if provider is None:
        return ProviderScanResult(provider_key="walmart", candidates=(), warnings=("Walmart provider is not registered.",))

    request = ProviderScanRequest(
        source_key="walmart",
        query=query.strip(),
        max_results=max_results,
        page=page,
        sort=sort_value,
        order=order_value,
        metadata={"requested_by": requested_by},
    )
    return await provider.scan(request)


async def run_preset_hunt(preset: HuntPreset, requested_by: str) -> tuple[list[DealCard], int, int, list[str]]:
    all_candidates: list[SourceCandidate] = []
    warnings: list[str] = []
    pages_checked = 0

    for query in preset.queries[:5]:
        result = await run_walmart_scan(
            query=query,
            page=1,
            max_results=10,
            sort_value=None,
            order_value=None,
            requested_by=requested_by,
        )
        pages_checked += 1
        all_candidates.extend(result.candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)

    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(dedupe_candidates(all_candidates)),
        warnings=tuple(warnings),
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=True,
    )
    cards = build_walmart_cards(aggregate, min_discount=preset.min_discount, alerts_only=False)
    cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
    return cards, pages_checked, len(all_candidates), warnings


def dedupe_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    unique: list[SourceCandidate] = []
    for candidate in candidates:
        key = candidate.product_id or candidate.product_url or candidate.title
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def build_hunt_menu_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔌 SniperPlug Deal Hunt",
        description="Pick a category. SniperPlug will search smart terms for you and show the best finds with View Deal buttons.",
        color=discord.Color.orange(),
    )
    for preset in HUNT_PRESETS.values():
        embed.add_field(
            name=f"{preset.emoji} {preset.label}",
            value=f"{preset.description}\nLooks for **{preset.min_discount}%+ off** finds.",
            inline=True,
        )
    embed.set_footer(text="Beginner mode: no provider names, no page numbers, no commands to memorize.")
    return embed


def build_preset_hunt_summary(
    preset: HuntPreset,
    pages_checked: int,
    products_checked: int,
    found_count: int,
    warnings: tuple[str, ...],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{preset.emoji} {preset.label} Hunt Results",
        description=(
            f"Checked: **{products_checked} products** from **{pages_checked} smart searches**\n"
            f"Filter: **{preset.min_discount}%+ off**\n"
            f"Found: **{found_count} candidate(s)**"
        ),
        color=discord.Color.red() if found_count else discord.Color.orange(),
    )
    embed.add_field(
        name="Searches used",
        value=", ".join(f"`{query}`" for query in preset.queries[:5]),
        inline=False,
    )
    if warnings:
        embed.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in warnings[:3]), inline=False)
    embed.set_footer(text="Preset hunts are broad. Use /deals search:your item for something specific.")
    return embed


def build_scan_summary(
    result: ProviderScanResult,
    query: str,
    min_discount: int,
    alerts_only: bool,
    simple_mode: bool,
) -> discord.Embed:
    total = f"{result.total_results:,}" if result.total_results is not None else "unknown"
    page_size = result.page_size or len(result.candidates) or 1
    start = result.start_index or ((result.page - 1) * page_size + 1)
    end = start + max(len(result.candidates) - 1, 0)
    next_text = "Tap Next Page" if result.has_next_page else "Not reported"
    title = "🔌 SniperPlug Deal Finder" if simple_mode else "🛒 Walmart Deal Scanner"

    embed = discord.Embed(
        title=title,
        description=(
            f"Searching: **{query}**\n"
            f"Showing: **{min_discount}%+ off**{' • alerts only' if alerts_only else ''}\n"
            f"Page: **{result.page}** • Results checked: **{start}-{end}** of **{total}**\n"
            f"More results: **{next_text}**"
        ),
        color=discord.Color.orange(),
    )
    if simple_mode:
        embed.add_field(
            name="How this search works",
            value=(
                "I search Walmart for your words, check returned products, then compare the current price against Walmart's returned regular/MSRP price. "
                "If Walmart does not return a real reference price, I cannot prove a huge discount from that item yet."
            ),
            inline=False,
        )
    if result.warnings:
        embed.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in result.warnings[:3]), inline=False)
    embed.set_footer(text="Prices can revert. Recheck before posting or buying. In-store stock needs a local stock check.")
    return embed


def build_hunt_summary(
    query: str,
    min_discount: int,
    pages_checked: int,
    candidates_checked: int,
    total_results: int | None,
    found_count: int,
    warnings: tuple[str, ...],
    simple_mode: bool,
) -> discord.Embed:
    total = f"{total_results:,}" if total_results is not None else "unknown"
    title = "🔥 80%+ Hunt Results" if simple_mode else "🔥 Advanced 80%+ Walmart Hunt"
    embed = discord.Embed(
        title=title,
        description=(
            f"Searching: **{query}**\n"
            f"Target: **{min_discount}%+ off**\n"
            f"Checked: **{candidates_checked} products across {pages_checked} page(s)** out of **{total}** possible results\n"
            f"Found: **{found_count} candidate(s)**"
        ),
        color=discord.Color.red() if found_count else discord.Color.orange(),
    )
    if warnings:
        embed.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in warnings[:3]), inline=False)
    embed.set_footer(text="80%+ hunt scans multiple result pages automatically. Still recheck checkout price before posting or buying.")
    return embed


def build_walmart_cards(result: ProviderScanResult, min_discount: int, alerts_only: bool) -> list[DealCard]:
    cards: list[DealCard] = []
    for candidate in result.candidates:
        decision = evaluate_candidate(candidate)
        deal = decision.deal
        discount = discount_percent(deal.current_price, deal.typical_price)
        if discount is None or discount < min_discount:
            continue
        if alerts_only and not decision.should_alert:
            continue
        embed = build_deal_card_embed(candidate, deal, decision, discount)
        cards.append(
            DealCard(
                embed=embed,
                url=deal.product_url,
                label=short_button_label(deal.title),
                score=decision.anomaly.score,
                discount=discount,
            )
        )
    return cards


def build_deal_card_embed(candidate: SourceCandidate, deal: NormalizedDeal, decision, discount: float) -> discord.Embed:
    score = decision.anomaly.score
    title = f"{heat_emoji(discount, deal.current_price)} {discount:.0f}% OFF • {trim_title(deal.title, 72)}"
    embed = discord.Embed(title=title, url=deal.product_url, color=embed_color(discount, score))
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    embed.add_field(name="💰 Price", value=price_block(deal.current_price, deal.typical_price), inline=False)
    embed.add_field(
        name="📊 Sniper Read",
        value=(
            f"**{friendly_score_level(decision.anomaly.level)}** • `{score}/250`\n"
            f"Route: **{route_label(decision.route.route)}**\n"
            f"Would alert: **{'Yes' if decision.should_alert else 'No'}**"
        ),
        inline=True,
    )
    embed.add_field(name="📦 Stock", value=stock_block(candidate), inline=True)
    embed.add_field(name="🟢 Liveness", value=liveness_block(deal, discount), inline=False)

    proof_lines = proof_lines_for(candidate, decision)
    if proof_lines:
        embed.add_field(name="🔎 Why it showed up", value="\n".join(proof_lines[:4]), inline=False)

    if deal.sku or deal.upc:
        embed.set_footer(text=f"SKU: {deal.sku or 'n/a'} • UPC: {deal.upc or 'n/a'} • Recheck before posting")
    else:
        embed.set_footer(text="Recheck before posting. Prices, stock, shipping, and account eligibility can change fast.")
    return embed


def no_match_help(query: str, min_discount: int, page: int, simple_mode: bool) -> str:
    if simple_mode:
        return (
            "That page did not have a proven discount high enough for this filter.\n"
            "Tap **Hunt 80%+** to scan several pages automatically, **Show More** to lower the discount filter, or **Next Page** to keep browsing."
        )
    return (
        "Try one of these next:\n"
        f"• `/walmart_scan query:{query} min_discount:{min_discount} page:{page + 1}`\n"
        f"• `/walmart_scan query:{query} min_discount:{max(0, min_discount - 20)} page:{page}`\n"
        "• Try tighter terms like `oled tv`, `gaming monitor`, `lego`, `patio`, `ssd`."
    )


def parse_sort_choice(value: str | None) -> tuple[str | None, str | None]:
    if value == "price_ascending":
        return "price", "ascending"
    if value == "price_descending":
        return "price", "descending"
    return value, None


def discount_percent(current_price: float | None, typical_price: float | None) -> float | None:
    if current_price is None or not typical_price or typical_price <= 0:
        return None
    return max(0.0, (typical_price - current_price) / typical_price * 100)


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def price_block(current_price: float | None, typical_price: float | None) -> str:
    if current_price is None:
        return "Current price unavailable"
    if typical_price:
        savings = typical_price - current_price
        return f"**{money(current_price)}**\nWas/typical: ~~{money(typical_price)}~~\nSave: **{money(savings)}**"
    return f"**{money(current_price)}**\nNo reference price returned."


def stock_block(candidate: SourceCandidate) -> str:
    lines = []
    if candidate.stock_status:
        lines.append(candidate.stock_status[:80])
    if candidate.can_add_to_cart is True:
        lines.append("🛒 Add-to-cart seen")
    elif candidate.can_add_to_cart is False:
        lines.append("🛒 Cart not confirmed")
    return "\n".join(lines) if lines else "Stock not confirmed"


def liveness_block(deal: NormalizedDeal, discount: float) -> str:
    if discount >= 80:
        return "🔥 **High-value candidate.** Re-run scan before posting because price errors can revert fast."
    if discount >= 50:
        return "💎 **Strong discount.** Verify checkout price and stock before posting."
    if discount >= 30:
        return "✅ **Useful discount.** Good for watchlist, but not a true glitch yet."
    if discount <= 10:
        return "⚪ **Weak/back-near-normal.** Usually not worth alerting unless there is another catalyst."
    return "🔎 Recheck before posting."


def proof_lines_for(candidate: SourceCandidate, decision) -> list[str]:
    lines = []
    for reason in decision.anomaly.reasons[:2]:
        lines.append(f"• {reason}")
    for signal in candidate.signals[:2]:
        lines.append(f"• {signal}")
    if not lines:
        lines.append("• Product link and current price returned by provider")
    return lines


def heat_emoji(discount: float, current_price: float | None) -> str:
    if current_price is not None and current_price <= 1:
        return "🚨"
    if discount >= 90:
        return "🚨"
    if discount >= 80:
        return "🔥"
    if discount >= 50:
        return "💎"
    return "✅"


def embed_color(discount: float, score: int) -> discord.Color:
    if discount >= 80 or score >= 140:
        return discord.Color.red()
    if discount >= 50 or score >= 100:
        return discord.Color.orange()
    return discord.Color.gold()


def friendly_score_level(level: str) -> str:
    labels = {
        "nuclear": "Extreme",
        "urgent": "Urgent",
        "strong": "Strong",
        "watch": "Watch",
        "ignore": "Low",
    }
    return labels.get(level, level.title())


def trim_title(title: str, limit: int) -> str:
    cleaned = " ".join(title.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def short_button_label(title: str) -> str:
    return trim_title(title, 32)
