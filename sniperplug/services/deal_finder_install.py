from __future__ import annotations

import discord

from sniperplug.services.deal_finder_engine import DealFinderResult, find_walmart_deals_for_query
from sniperplug.services.deal_finder_telemetry import top_route_lines


_PATCHED = False
_ORIGINAL_SEND_WALMART_SCAN = None


def install_unified_deal_finder() -> None:
    """Route beginner `/deals` through the expanded shared deal finder engine."""
    global _PATCHED, _ORIGINAL_SEND_WALMART_SCAN
    if _PATCHED:
        return

    from sniperplug.cogs import deal_scanner

    _ORIGINAL_SEND_WALMART_SCAN = deal_scanner.DealScannerCog._send_walmart_scan
    deal_scanner.DealScannerCog._send_walmart_scan = _send_walmart_scan_with_deal_finder
    _PATCHED = True


async def _send_walmart_scan_with_deal_finder(self, interaction, query: str, min_discount: int, page: int, max_results: int, sort_value, order_value, alerts_only: bool, simple_mode: bool):
    # Keep advanced/staff scans and paged reruns on the existing precise scan path.
    if not simple_mode or page != 1 or sort_value is not None or order_value is not None:
        return await _ORIGINAL_SEND_WALMART_SCAN(self, interaction, query, min_discount, page, max_results, sort_value, order_value, alerts_only, simple_mode)

    from sniperplug.cogs import deal_scanner
    from sniperplug.providers.base import ProviderStatus
    from sniperplug.providers.registry import provider_registry
    from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards

    provider = provider_registry.get("walmart")
    if provider is None:
        await interaction.followup.send("Walmart search is not connected yet.", ephemeral=True)
        return
    health = await provider.healthcheck()
    if health.status != ProviderStatus.READY:
        await interaction.followup.send("Deal search is not ready yet. Staff needs to finish the Walmart connection first.", ephemeral=True)
        return

    result = await find_walmart_deals_for_query(
        query=query,
        requested_by=str(interaction.user.id),
        min_discount=min_discount,
        db=getattr(self.bot, "db", None),
        guild_id=interaction.guild_id,
    )
    summary = build_deal_finder_summary(result)

    shown_verified = result.verified_cards[:5]
    shown_review = result.review_candidates.cards[:5]
    if shown_verified:
        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=interaction.guild_id,
            cards=shown_verified,
            source_label="deals:unified_finder",
            fallback_retailer="walmart",
        )
        deal_scanner.add_public_posting_field(summary, public_result)
        summary.add_field(name="Product links", value="Each verified card includes its own **App/Web** and **Browser Search** links.", inline=False)
        await interaction.followup.send(
            embeds=[summary] + [card.embed for card in shown_verified],
            view=deal_scanner.DealSearchControlView(query, page, max(0, min_discount), max_results, sort_value, order_value, alerts_only, simple_mode, shown_verified, result.aggregate.has_next_page),
            ephemeral=True,
        )
        if shown_review:
            await interaction.followup.send(
                content="🟨 Extra review/flip leads — private only, not public-posted as verified deals.",
                embeds=[card.embed for card in shown_review],
                view=deal_scanner.PresetResultView(shown_review),
                ephemeral=True,
            )
        return

    if shown_review:
        await interaction.followup.send(
            embeds=[summary] + [card.embed for card in shown_review],
            view=deal_scanner.PresetResultView(shown_review),
            ephemeral=True,
        )
        return

    summary.add_field(name="Nothing useful found yet", value=deal_scanner.no_match_help(query, min_discount, page, simple_mode), inline=False)
    await interaction.followup.send(
        embed=summary,
        view=deal_scanner.DealSearchControlView(query, page, max(0, min_discount), max_results, sort_value, order_value, alerts_only, simple_mode),
        ephemeral=True,
    )


def build_deal_finder_summary(result: DealFinderResult) -> discord.Embed:
    review_count = len(result.review_candidates.cards)
    embed = discord.Embed(
        title="🔌 SniperPlug Deal Finder",
        description=(
            f"Searching: **{result.query}**\n"
            f"Expanded searches: **{len(result.search_plan.queries)}** • API calls: **{result.searches_attempted}**\n"
            f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} result pages**\n"
            f"Verified {result.min_discount}%+ deals: **{len(result.verified_cards)}**\n"
            f"Review/flip leads: **{review_count}**"
        ),
        color=discord.Color.red() if result.verified_cards else discord.Color.dark_gold() if review_count else discord.Color.orange(),
    )
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
    embed.set_footer(text="Verified cards can public-post. Review/flip leads are private and require manual checkout/comp checks.")
    return embed
