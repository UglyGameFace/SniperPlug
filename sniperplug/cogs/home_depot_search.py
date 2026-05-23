from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.penny_score import score_penny_candidate
from sniperplug.services.quota_guard import serpapi_quota_guard


class HomeDepotSearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="home_depot_search", description="Manually search Home Depot through SerpApi with quota protection.")
    @app_commands.describe(
        query="Product search, like milwaukee drill, vanity, faucet, ceiling fan.",
        store_id="Optional Home Depot store ID.",
        zip_code="Optional delivery/local ZIP.",
        page="Page to scan. Page 2+ uses another SerpApi credit.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def home_depot_search(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None = None,
        zip_code: str | None = None,
        page: app_commands.Range[int, 1, 5] = 1,
    ) -> None:
        await self._run_search(interaction, query=query, store_id=store_id, zip_code=zip_code, page=page, penny_mode=False)

    @app_commands.command(name="home_depot_penny_hunt", description="Manually hunt Home Depot penny/clearance candidates through SerpApi.")
    @app_commands.describe(
        query="Targeted query, like faucet, vanity, milwaukee, ryobi, ceiling fan.",
        store_id="Recommended Home Depot store ID.",
        zip_code="Recommended local ZIP.",
        page="Page to scan. Page 2+ uses another SerpApi credit.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def home_depot_penny_hunt(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None = None,
        zip_code: str | None = None,
        page: app_commands.Range[int, 1, 5] = 1,
    ) -> None:
        await self._run_search(interaction, query=query, store_id=store_id, zip_code=zip_code, page=page, penny_mode=True)

    async def _run_search(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None,
        zip_code: str | None,
        page: int,
        penny_mode: bool,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        provider = provider_registry.get("home_depot_serpapi")
        if provider is None:
            await interaction.followup.send("Home Depot SerpApi provider is not registered yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(health.message, ephemeral=True)
            return

        broad_warning = _broad_query_warning(query)
        quota = serpapi_quota_guard.check(interaction.user.id, cost=1)
        if not quota.allowed:
            await interaction.followup.send(f"SerpApi scan blocked: {quota.reason}", ephemeral=True)
            return

        result = await provider.scan(
            ProviderScanRequest(
                source_key="home_depot_serpapi",
                query=query.strip(),
                page=page,
                max_results=24,
                metadata={
                    "store_id": (store_id or "").strip(),
                    "zip_code": (zip_code or "").strip(),
                    "requested_by": str(interaction.user.id),
                },
            )
        )
        quota_after = serpapi_quota_guard.record(interaction.user.id, cost=1)

        if result.warnings and not result.candidates:
            await interaction.followup.send("\n".join(result.warnings), ephemeral=True)
            return

        cards = build_home_depot_cards(result.candidates, has_store_id=bool(store_id), penny_mode=penny_mode)
        summary = discord.Embed(
            title="🏚️ Home Depot Penny Hunt" if penny_mode else "🏚️ Home Depot Search",
            description=(
                f"Query: **{query}**\n"
                f"Store: `{store_id or 'n/a'}` • ZIP: `{zip_code or 'n/a'}` • Page: `{page}`\n"
                f"SerpApi used: **{quota_after.monthly_used}/{quota_after.monthly_limit} monthly safe budget** • "
                f"**{quota_after.daily_used}/{quota_after.daily_limit} today**\n"
                "SerpApi results are **not** in-store scan confirmation. Route strong candidates to staff review."
            ),
            color=discord.Color.orange(),
        )
        if broad_warning:
            summary.add_field(name="Quota warning", value=broad_warning, inline=False)
        if result.warnings:
            summary.add_field(name="Provider notes", value="\n".join(result.warnings[:3]), inline=False)
        if not cards:
            summary.add_field(name="No candidates", value="No products came back from this search. Try a tighter query or another store/ZIP.", inline=False)
            await interaction.followup.send(embed=summary, ephemeral=True)
            return

        await interaction.followup.send(embeds=[summary] + cards[:5], ephemeral=True)


def build_home_depot_cards(candidates: tuple[SourceCandidate, ...], *, has_store_id: bool, penny_mode: bool) -> list[discord.Embed]:
    scored: list[tuple[int, discord.Embed]] = []
    for candidate in candidates:
        penny = score_penny_candidate(candidate, has_store_id=has_store_id)
        if penny_mode and penny.score < 25:
            continue
        embed = discord.Embed(
            title=f"{_money(candidate.current_price)} • {candidate.title[:75]}",
            url=candidate.product_url,
            color=discord.Color.red() if penny.score >= 60 else discord.Color.orange(),
        )
        if candidate.image_url:
            embed.set_thumbnail(url=candidate.image_url)
        embed.add_field(name="Penny score", value=f"**{penny.score}/100**\n`{penny.level}`", inline=True)
        embed.add_field(name="IDs", value=f"SKU/Product: `{candidate.sku or candidate.product_id or 'n/a'}`", inline=True)
        if candidate.stock_status:
            embed.add_field(name="Availability", value=candidate.stock_status[:300], inline=False)
        if candidate.signals:
            embed.add_field(name="Signals", value="\n".join(f"• {signal}" for signal in candidate.signals[:5]), inline=False)
        if penny.reasons:
            embed.add_field(name="Score reasons", value="\n".join(f"• {reason}" for reason in penny.reasons[:5]), inline=False)
        embed.set_footer(text="Not confirmed until in-store scan/register proof. Save promising leads with /seed_clearance.")
        scored.append((penny.score, embed))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [embed for _, embed in scored]


def _money(value: float | None) -> str:
    if value is None:
        return "No price"
    return f"${value:,.2f}"


def _broad_query_warning(query: str) -> str | None:
    if query.strip().lower() in {"clearance", "sale", "tools", "home depot", "deal", "deals"}:
        return "This is a broad query and can waste credits. Use tighter terms like `milwaukee drill`, `faucet`, `vanity`, or `ceiling fan`."
    return None
