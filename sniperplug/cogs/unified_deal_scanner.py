from __future__ import annotations

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealScannerCog
from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
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
    ) -> None:
        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("Walmart search is not connected yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send("Deal search is not ready yet. Staff needs to finish the Walmart connection first.", ephemeral=True)
            return

        starting_discount = await get_starting_deal_percent(getattr(self.bot, "db", None), interaction.guild_id, fallback=min_discount)
        result = await find_walmart_deals_for_query(
            query=query,
            requested_by=str(interaction.user.id),
            min_discount=starting_discount,
            db=getattr(self.bot, "db", None),
            guild_id=interaction.guild_id,
        )
        ranked = rank_for_search_mode(result.verified_cards, result.review_candidates.cards, mode, limit=5)
        summary = build_deal_finder_summary(result, ranked=ranked)
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
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.verified], view=view, ephemeral=True)
            if ranked.has_review:
                await interaction.followup.send(
                    content="🟨 Extra review/raw/flip/scout leads — private only. Staff can manually publish one after checking it.",
                    embeds=[card.embed for card in ranked.review],
                    view=ManualReviewShareView(ranked.review),
                    ephemeral=True,
                )
            return

        if ranked.has_review:
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.review], view=view, ephemeral=True)
            return

        summary.add_field(name="Nothing useful found yet", value=deal_scanner.no_match_help(query, starting_discount, page, simple_mode), inline=False)
        await interaction.followup.send(embed=summary, view=view, ephemeral=True)


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
        self.add_item(DealModeButton(MODE_BEST, row=0, disabled=self.mode == MODE_BEST))
        self.add_item(DealModeButton(MODE_POPULAR, row=0, disabled=self.mode == MODE_POPULAR))
        self.add_item(DealModeButton(MODE_HIDDEN, row=0, disabled=self.mode == MODE_HIDDEN))
        self.add_item(DealModeButton(MODE_CHEAPEST, row=1, disabled=self.mode == MODE_CHEAPEST))
        self.add_item(DealModeButton(MODE_MARKDOWN, row=1, disabled=self.mode == MODE_MARKDOWN))
        self.add_item(RefreshDealModeButton(row=1))

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
            )
            return

        ranked = rank_for_search_mode(self.result.verified_cards, self.result.review_candidates.cards, mode, limit=5)
        summary = build_deal_finder_summary(self.result, ranked=ranked)
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
        )
        if ranked.has_verified:
            summary.add_field(name="Mode switched", value="Re-ranked the same scan results without spending another API search.", inline=False)
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.verified], view=view, ephemeral=True)
            if ranked.has_review:
                await interaction.followup.send(
                    content="🟨 Extra review/raw/flip/scout leads — private only. Staff can manually publish one after checking it.",
                    embeds=[card.embed for card in ranked.review],
                    view=ManualReviewShareView(ranked.review),
                    ephemeral=True,
                )
            return
        if ranked.has_review:
            await interaction.followup.send(embeds=[summary] + [card.embed for card in ranked.review], view=view, ephemeral=True)
            return
        summary.add_field(name="No cards for this mode", value="Try another mode or refresh the search.", inline=False)
        await interaction.followup.send(embed=summary, view=view, ephemeral=True)


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


def build_deal_finder_summary(result: DealFinderResult, *, ranked: ModeRankedCards | None = None) -> discord.Embed:
    review_count = len(result.review_candidates.cards)
    scout_count = getattr(result, "scout_lead_count", 0)
    mode = ranked.mode if ranked else DEAL_SEARCH_MODES[MODE_BEST]
    embed = discord.Embed(
        title=f"🔌 SniperPlug Deal Finder • {mode.display_name}",
        description=(
            f"Searching: **{result.query}**\n"
            f"Mode: **{mode.label}** — {mode.description}\n"
            f"Starting threshold: **{result.min_discount}%+ verified markdown**\n"
            f"Expanded searches: **{len(result.search_plan.queries)}** • API calls: **{result.searches_attempted}**\n"
            f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} result pages**\n"
            f"Verified {result.min_discount}%+ deals: **{len(result.verified_cards)}**\n"
            f"Review/raw/flip/scout leads: **{review_count}**\n"
            f"Low-price scout leads found: **{scout_count}**"
        ),
        color=discord.Color.red() if result.verified_cards else discord.Color.dark_gold() if review_count else discord.Color.orange(),
    )
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
    embed.set_footer(text="Buttons re-rank results by mode. Fresh Scan reruns the search. Change starting markdown with /deal_threshold.")
    return embed
