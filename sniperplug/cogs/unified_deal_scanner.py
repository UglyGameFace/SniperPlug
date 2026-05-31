from __future__ import annotations

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealScannerCog
from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.active_deal_cache import (
    CachedDealRow,
    ScanFreshness,
    active_cache_snapshot,
    build_cached_active_embed,
    build_new_since_scan_embed,
    classify_scan_freshness,
    list_cached_active_deals,
)
from sniperplug.services.deal_finder_engine import DealFinderResult, find_walmart_deals_for_query
from sniperplug.services.deal_finder_telemetry import top_route_lines
from sniperplug.services.deal_search_modes import (
    DEAL_SEARCH_MODES,
    MODE_BEST,
    MODE_CHEAPEST,
    MODE_HIDDEN,
    MODE_MARKDOWN,
    MODE_POPULAR,
    ModeRankedCards,
    normalize_deal_search_mode,
    rank_for_search_mode,
)
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.manual_review_share import ManualReviewShareView
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards


class UnifiedDealScannerCog(DealScannerCog):
    """Deal scanner with the unified beginner `/deals` flow wired directly."""

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
        if not simple_mode or page != 1 or sort_value is not None or order_value is not None:
            return await super()._send_walmart_scan(
                interaction,
                query,
                min_discount,
                page,
                max_results,
                sort_value,
                order_value,
                alerts_only,
                simple_mode,
            )
        await self._send_unified_deal_search(
            interaction,
            query=query,
            min_discount=min_discount,
            page=page,
            max_results=max_results,
            sort_value=sort_value,
            order_value=order_value,
            alerts_only=alerts_only,
            simple_mode=simple_mode,
            mode=MODE_BEST,
        )

    async def _send_unified_deal_search(
        self,
        interaction: discord.Interaction,
        *,
        query: str,
        min_discount: int,
        page: int,
        max_results: int,
        sort_value: str | None,
        order_value: str | None,
        alerts_only: bool,
        simple_mode: bool,
        mode: str,
        force_refresh: bool = False,
    ) -> None:
        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("Walmart search is not connected yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send("Deal search is not ready yet. Staff needs to finish the Walmart connection first.", ephemeral=True)
            return

        db = getattr(self.bot, "db", None)
        cached_rows: list[CachedDealRow] = []
        cached_before = {}
        if db is not None and interaction.guild_id is not None:
            cached_rows = await list_cached_active_deals(db, interaction.guild_id, retailer="walmart", query=query, limit=6)
            cached_before = await active_cache_snapshot(db, interaction.guild_id, retailer="walmart", query=query, limit=100)
            if cached_rows and not force_refresh:
                await interaction.followup.send(embed=build_cached_active_embed(query, cached_rows), ephemeral=True)

        starting_discount = await get_starting_deal_percent(db, interaction.guild_id, fallback=min_discount)
        result = await find_walmart_deals_for_query(
            query=query,
            requested_by=str(interaction.user.id),
            min_discount=starting_discount,
            db=db,
            guild_id=interaction.guild_id,
            force_refresh=force_refresh,
        )
        ranked = rank_for_search_mode(result.verified_cards, result.review_candidates.cards, mode, limit=5)
        freshness = classify_scan_freshness([*result.verified_cards, *result.review_candidates.cards], cached_before, fallback_retailer="walmart")
        summary = build_deal_finder_summary(result, ranked=ranked, freshness=freshness)
        view = DealSearchModeView(
            cog=self,
            query=query,
            min_discount=starting_discount,
            max_results=max_results,
            sort_value=sort_value,
            order_value=order_value,
            alerts_only=alerts_only,
            simple_mode=simple_mode,
            mode=ranked.mode.key,
            result=result,
            ranked=ranked,
            cached_rows=cached_rows,
            freshness=freshness,
        )

        if ranked.has_verified:
            public_result = await maybe_post_public_deal_cards(
                bot=self.bot,
                guild_id=interaction.guild_id,
                cards=ranked.verified,
                source_label=f"deals:unified_finder:{ranked.mode.key}",
                fallback_retailer="walmart",
            )
            deal_scanner.add_public_posting_field(summary, public_result)
            summary.add_field(name="Product links", value="Each verified card includes its own **App/Web** and **Browser Search** links.", inline=False)
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.verified], ephemeral=True)
            if ranked.has_review:
                await interaction.followup.send(
                    content="🟨 Extra review/raw/flip/scout leads — private only. Staff can manually publish one after checking it.",
                    embeds=[card.embed for card in ranked.review],
                    view=ManualReviewShareView(ranked.review),
                    ephemeral=True,
                )
            await send_deal_mode_controls(interaction, view, ranked, freshness)
            return

        if ranked.has_review:
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.review], ephemeral=True)
            await send_deal_mode_controls(interaction, view, ranked, freshness)
            return

        summary.add_field(name="Nothing useful found yet", value=deal_scanner.no_match_help(query, starting_discount, page, simple_mode), inline=False)
        await interaction.followup.send(embed=summary, ephemeral=True)
        await send_deal_mode_controls(interaction, view, ranked, freshness)


class DealSearchModeView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: UnifiedDealScannerCog,
        query: str,
        min_discount: int,
        max_results: int,
        sort_value: str | None,
        order_value: str | None,
        alerts_only: bool,
        simple_mode: bool,
        mode: str,
        result: DealFinderResult | None = None,
        ranked: ModeRankedCards | None = None,
        cached_rows: list[CachedDealRow] | None = None,
        freshness: ScanFreshness | None = None,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.query = query
        self.min_discount = min_discount
        self.max_results = max_results
        self.sort_value = sort_value
        self.order_value = order_value
        self.alerts_only = alerts_only
        self.simple_mode = simple_mode
        self.mode = normalize_deal_search_mode(mode).key
        self.result = result
        self.ranked = ranked
        self.cached_rows = cached_rows or []
        self.freshness = freshness
        self.add_item(DealModeButton(MODE_BEST, row=0, disabled=self.mode == MODE_BEST))
        self.add_item(DealModeButton(MODE_POPULAR, row=0, disabled=self.mode == MODE_POPULAR))
        self.add_item(DealModeButton(MODE_HIDDEN, row=0, disabled=self.mode == MODE_HIDDEN))
        self.add_item(DealModeButton(MODE_CHEAPEST, row=1, disabled=self.mode == MODE_CHEAPEST))
        self.add_item(DealModeButton(MODE_MARKDOWN, row=1, disabled=self.mode == MODE_MARKDOWN))
        self.add_item(RefreshDealModeButton(row=1))
        self.add_item(CachedActiveButton(row=2, disabled=not bool(self.cached_rows)))
        self.add_item(NewSinceScanButton(row=2, disabled=not bool(self.freshness and (self.freshness.new_count or self.freshness.price_drop_count))))

    async def show_mode(self, interaction: discord.Interaction, mode: str, *, refresh: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        if refresh or self.result is None:
            await self.cog._send_unified_deal_search(
                interaction,
                query=self.query,
                min_discount=self.min_discount,
                page=1,
                max_results=self.max_results,
                sort_value=self.sort_value,
                order_value=self.order_value,
                alerts_only=self.alerts_only,
                simple_mode=self.simple_mode,
                mode=mode,
                force_refresh=refresh,
            )
            return

        ranked = rank_for_search_mode(self.result.verified_cards, self.result.review_candidates.cards, mode, limit=5)
        summary = build_deal_finder_summary(self.result, ranked=ranked, freshness=self.freshness)
        view = DealSearchModeView(
            cog=self.cog,
            query=self.query,
            min_discount=self.min_discount,
            max_results=self.max_results,
            sort_value=self.sort_value,
            order_value=self.order_value,
            alerts_only=self.alerts_only,
            simple_mode=self.simple_mode,
            mode=ranked.mode.key,
            result=self.result,
            ranked=ranked,
            cached_rows=self.cached_rows,
            freshness=self.freshness,
        )
        if ranked.has_verified:
            summary.add_field(name="Mode switched", value="Re-ranked the same scan results without spending another API search.", inline=False)
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.verified], ephemeral=True)
            if ranked.has_review:
                await interaction.followup.send(
                    content="🟨 Extra review/raw/flip/scout leads — private only. Staff can manually publish one after checking it.",
                    embeds=[card.embed for card in ranked.review],
                    view=ManualReviewShareView(ranked.review),
                    ephemeral=True,
                )
            await send_deal_mode_controls(interaction, view, ranked, self.freshness)
            return
        if ranked.has_review:
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.review], ephemeral=True)
            await send_deal_mode_controls(interaction, view, ranked, self.freshness)
            return
        summary.add_field(name="No cards for this mode", value="Try another mode or refresh the search.", inline=False)
        await interaction.followup.send(embed=summary, ephemeral=True)
        await send_deal_mode_controls(interaction, view, ranked, self.freshness)


class DealModeButton(discord.ui.Button):
    def __init__(self, mode: str, *, row: int, disabled: bool = False):
        info = DEAL_SEARCH_MODES[mode]
        super().__init__(label=info.label, emoji=info.emoji, style=discord.ButtonStyle.primary if mode == MODE_BEST else discord.ButtonStyle.secondary, row=row, disabled=disabled, custom_id=f"deal_mode:{mode}")
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView):
            await interaction.response.send_message("This result menu is no longer active.", ephemeral=True)
            return
        await self.view.show_mode(interaction, self.mode)


class RefreshDealModeButton(discord.ui.Button):
    def __init__(self, *, row: int):
        super().__init__(label="Fresh Scan", emoji="🔄", style=discord.ButtonStyle.secondary, row=row, custom_id="deal_mode:refresh")

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView):
            await interaction.response.send_message("This result menu is no longer active.", ephemeral=True)
            return
        await self.view.show_mode(interaction, self.view.mode, refresh=True)


class CachedActiveButton(discord.ui.Button):
    def __init__(self, *, row: int, disabled: bool = False):
        super().__init__(label="Cached Active", emoji="⚡", style=discord.ButtonStyle.success, row=row, disabled=disabled, custom_id="deal_mode:cached_active")

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView):
            await interaction.response.send_message("This result menu is no longer active.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_cached_active_embed(self.view.query, self.view.cached_rows), ephemeral=True)


class NewSinceScanButton(discord.ui.Button):
    def __init__(self, *, row: int, disabled: bool = False):
        super().__init__(label="New / Drops", emoji="🆕", style=discord.ButtonStyle.danger, row=row, disabled=disabled, custom_id="deal_mode:new_since_scan")

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView) or self.view.freshness is None:
            await interaction.response.send_message("This result menu is no longer active.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        freshness = self.view.freshness
        cards = [*freshness.price_drop_cards[:5], *freshness.new_cards[:5]][:5]
        await interaction.followup.send(embed=build_new_since_scan_embed(self.view.query, freshness), ephemeral=True)
        if not cards:
            return
        for card in cards:
            await interaction.followup.send(embed=card.embed, ephemeral=True)


async def send_deal_mode_controls(interaction: discord.Interaction, view: DealSearchModeView, ranked: ModeRankedCards, freshness: ScanFreshness | None = None) -> None:
    await interaction.followup.send(embed=build_deal_mode_menu_embed(view.query, ranked, freshness), view=view, ephemeral=True)


def build_deal_mode_menu_embed(query: str, ranked: ModeRankedCards, freshness: ScanFreshness | None = None) -> discord.Embed:
    mode = ranked.mode
    freshness_line = ""
    if freshness:
        freshness_line = f"\nNew: **{freshness.new_count}** • Price drops: **{freshness.price_drop_count}** • Repeats: **{freshness.repeat_count}**"
    embed = discord.Embed(
        title=f"⚙️ Deal result menu • {mode.display_name}",
        description=(
            f"Search: **{query}**\n"
            f"Current mode: **{mode.label}**{freshness_line}\n\n"
            "Tap a mode below to re-rank these same scan results. "
            "**Fresh Scan** bypasses the short cache and spends another API pass."
        ),
        color=discord.Color.blurple() if mode.public_safe else discord.Color.dark_gold(),
    )
    embed.add_field(
        name="Modes",
        value=(
            "🔥 **Best Picks** — balanced default\n"
            "🏷️ **Popular Brands** — known brands/trusted sellers first\n"
            "🧪 **Hidden Gems** — offbrand/scout/review-only leads\n"
            "💸 **Cheapest** — lowest current price first\n"
            "📉 **Biggest Markdown** — strongest verified markdowns\n"
            "⚡ **Cached Active** — recently seen active deals, no API pass\n"
            "🆕 **New / Drops** — only brand-new or lower-price cards from this scan"
        ),
        inline=False,
    )
    if not mode.public_safe:
        embed.add_field(
            name="Private review mode",
            value="Hidden Gem results are broad and stay private unless staff manually shares one.",
            inline=False,
        )
    embed.set_footer(text="Short scan cache speeds repeats. Fresh Scan bypasses cache so new glitches still get checked.")
    return embed


def build_deal_finder_summary(result: DealFinderResult, *, ranked: ModeRankedCards | None = None, freshness: ScanFreshness | None = None) -> discord.Embed:
    review_count = len(result.review_candidates.cards)
    scout_count = getattr(result, "scout_lead_count", 0)
    mode = ranked.mode if ranked else DEAL_SEARCH_MODES[MODE_BEST]
    embed = discord.Embed(
        title=f"🔌 SniperPlug Deal Finder • {mode.display_name}",
        description=(
            f"Searching: **{result.query}**\n"
            f"Mode: **{mode.label}** — {mode.description}\n"
            f"Starting threshold: **{result.min_discount}%+ verified markdown**\n"
            f"Expanded searches: **{len(result.search_plan.queries)}** • Fresh API calls: **{result.searches_attempted}** • Cache hits: **{getattr(result, 'cache_hits', 0)}**\n"
            f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} result pages**\n"
            f"Verified {result.min_discount}%+ deals: **{len(result.verified_cards)}**\n"
            f"Review/raw/flip/scout leads: **{review_count}**\n"
            f"Low-price scout leads found: **{scout_count}**"
        ),
        color=discord.Color.red() if result.verified_cards else discord.Color.dark_gold() if review_count else discord.Color.orange(),
    )
    if freshness:
        embed.add_field(
            name="Cache freshness",
            value=f"Cached before scan: **{freshness.cached_before}** • New: **{freshness.new_count}** • Price drops: **{freshness.price_drop_count}** • Repeat same/higher: **{freshness.repeat_count}**",
            inline=False,
        )
    if getattr(result, "force_refresh", False):
        embed.add_field(name="Fresh Scan", value="This scan bypassed the short route cache and rechecked Walmart routes live.", inline=False)
    if ranked:
        embed.add_field(name="Mode note", value=ranked.note, inline=False)
        if not ranked.mode.public_safe:
            embed.add_field(name="Private review mode", value="Hidden Gem results are intentionally broader and stay private unless staff manually publishes one after checking seller, reviews, variants, and comps.", inline=False)
    if result.search_plan.queries:
        embed.add_field(
            name="Search plan",
            value=", ".join(f"`{query}`" for query in result.search_plan.queries[:6]),
            inline=False,
        )
    if result.boosted_routes:
        embed.add_field(
            name="🧠 Learned route boost",
            value=", ".join(f"`{query}`" for query in result.boosted_routes[:3]),
            inline=False,
        )
    route_lines = top_route_lines(result.route_stats, limit=5)
    if route_lines:
        embed.add_field(name="🧭 Productive routes", value="\n".join(route_lines), inline=False)
    if result.search_plan.notes:
        embed.add_field(name="Expansion notes", value="\n".join(f"• {note}" for note in result.search_plan.notes[:4]), inline=False)
    if result.review_candidates:
        embed.add_field(name="🟨 Review / flip audit", value=result.review_candidates.summary_line(), inline=False)
    if result.warnings:
        embed.add_field(name="⚠️ API notes", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
    embed.set_footer(text="Buttons re-rank results by mode. Fresh Scan bypasses cache. Change starting markdown with /deal_threshold.")
    return embed
