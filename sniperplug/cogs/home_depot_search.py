from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderScanResult, ProviderStatus
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

        scan_failed = _scan_failed_before_success(result)
        quota_after = quota if scan_failed else serpapi_quota_guard.record(interaction.user.id, cost=1)

        if scan_failed:
            await interaction.followup.send("\n".join(result.warnings), ephemeral=True)
            return

        raw_count = len(result.candidates)
        cards = build_home_depot_cards(result.candidates, has_store_id=bool(store_id), penny_mode=penny_mode)
        summary = discord.Embed(
            title="🏚️ Home Depot Penny Hunt" if penny_mode else "🏚️ Home Depot Search",
            description=(
                f"Searching: **{query}**\n"
                f"Store: `{store_id or 'n/a'}` • ZIP: `{zip_code or 'n/a'}` • Page: `{page}`\n"
                f"SerpApi used: **{quota_after.monthly_used}/{quota_after.monthly_limit} monthly safe budget** • "
                f"**{quota_after.daily_used}/{quota_after.daily_limit} today**\n"
                f"Raw products parsed: **{raw_count}**\n"
                "Showing SniperPlug-style cards. These are **verification candidates**, not confirmed in-store penny deals."
            ),
            color=discord.Color.orange(),
        )
        if broad_warning:
            summary.add_field(name="⚠️ Quota warning", value=broad_warning, inline=False)
        if penny_mode and not store_id:
            summary.add_field(
                name="⚠️ Store ID recommended",
                value="Penny/local clearance scoring is much weaker without a specific Home Depot store ID. ZIP alone can still return shippable/area results, but it is not store proof.",
                inline=False,
            )
        if result.warnings:
            summary.add_field(name="⚠️ Provider notes", value="\n".join(result.warnings[:3]), inline=False)
        if raw_count and not cards:
            summary.add_field(
                name="Weak results shown anyway",
                value="Products came back, but none scored as strong penny candidates. SniperPlug will still show the best raw results so a paid SerpApi credit never looks like nothing happened.",
                inline=False,
            )
            cards = build_home_depot_cards(result.candidates, has_store_id=bool(store_id), penny_mode=False)
        if not cards:
            summary.add_field(
                name="No products returned",
                value="SerpApi returned 0 usable Home Depot products for this exact query/store/ZIP/page. Try adding `store_id`, broadening the query, or using `/home_depot_search` first.",
                inline=False,
            )
            await interaction.followup.send(embed=summary, ephemeral=True)
            return

        await interaction.followup.send(embeds=[summary] + cards[:5], ephemeral=True)


def build_home_depot_cards(candidates: tuple[SourceCandidate, ...], *, has_store_id: bool, penny_mode: bool) -> list[discord.Embed]:
    scored: list[tuple[int, discord.Embed]] = []
    for candidate in candidates:
        penny = score_penny_candidate(candidate, has_store_id=has_store_id)
        embed = build_home_depot_deal_card(candidate, penny.score, penny.level, penny.reasons)
        scored.append((penny.score, embed))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [embed for _, embed in scored]


def build_home_depot_deal_card(candidate: SourceCandidate, penny_score: int, penny_level: str, reasons: tuple[str, ...]) -> discord.Embed:
    title = f"{home_depot_heat_emoji(candidate.current_price, penny_score)} {home_depot_label(penny_score)} • {trim_title(candidate.title, 72)}"
    embed = discord.Embed(title=title, url=candidate.product_url, color=home_depot_color(penny_score))
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)

    embed.add_field(name="💰 Price", value=home_depot_price_block(candidate), inline=False)
    embed.add_field(
        name="📊 Sniper Read",
        value=(
            f"**{friendly_penny_level(penny_level)}** • `{penny_score}/100`\n"
            "Route: **Staff Review**\n"
            "Would alert: **No**"
        ),
        inline=True,
    )
    embed.add_field(name="📦 Stock", value=home_depot_stock_block(candidate), inline=True)
    embed.add_field(name="🟢 Liveness", value=home_depot_liveness_block(candidate.current_price, penny_score), inline=False)

    proof_lines = home_depot_proof_lines(candidate, reasons)
    if proof_lines:
        embed.add_field(name="🔎 Why it showed up", value="\n".join(proof_lines[:6]), inline=False)

    footer_bits = [f"SKU: {candidate.sku or candidate.product_id or 'n/a'}"]
    if candidate.model:
        footer_bits.append(f"Model: {candidate.model}")
    if candidate.upc:
        footer_bits.append(f"UPC: {candidate.upc}")
    footer_bits.append("SerpApi candidate")
    footer_bits.append("Verify in store before posting")
    embed.set_footer(text=" • ".join(footer_bits))
    return embed


def home_depot_price_block(candidate: SourceCandidate) -> str:
    current_price = candidate.current_price
    if current_price is None:
        return "Current price unavailable\nNo Home Depot reference price returned."
    ending = price_ending(current_price)
    ending_line = f"\nEnding: **.{ending}**" if ending else ""
    typical = candidate.typical_price
    if typical and typical > current_price:
        savings = typical - current_price
        discount = round((savings / typical) * 100)
        return f"**{money(current_price)}**\nWas/typical: ~~{money(typical)}~~\nSave: **{money(savings)} ({discount}%)**{ending_line}"
    attrs = candidate.variant_attributes or {}
    savings_text = attrs.get("price_saving") or attrs.get("percentage_off")
    if savings_text:
        return f"**{money(current_price)}**\nWas/typical: **Not returned**\nSavings signal: **{savings_text}**{ending_line}"
    return f"**{money(current_price)}**\nWas/typical: **Not returned**\nSave: **Unknown**{ending_line}"


def home_depot_stock_block(candidate: SourceCandidate) -> str:
    lines: list[str] = []
    attrs = candidate.variant_attributes or {}
    if candidate.stock_status:
        lines.append(candidate.stock_status[:120])
    if candidate.can_add_to_cart is not None:
        lines.append(f"Add-to-cart: {'seen' if candidate.can_add_to_cart else 'not seen'}")
    for key in ("pickup", "delivery"):
        if attrs.get(key):
            lines.append(attrs[key][:120])
    for signal in candidate.signals:
        if signal.startswith("store_id:") or signal.startswith("zip:"):
            lines.append(signal)
    return "\n".join(lines[:5]) if lines else "Local stock not confirmed"


def home_depot_liveness_block(current_price: float | None, penny_score: int) -> str:
    if current_price is not None and price_ending(current_price) == "01":
        return "🚨 **Possible penny candidate.** SerpApi is not register proof; verify with in-store scan/register before posting."
    if penny_score >= 80:
        return "🔥 **High-priority verification candidate.** Do not public-alert until in-store proof confirms it."
    if penny_score >= 60:
        return "💎 **Strong clearance candidate.** Send to staff review and verify locally."
    if penny_score >= 30:
        return "✅ **Clearance watch.** Useful lead, but not a confirmed glitch or penny deal."
    return "⚪ **Weak lead.** Showing because a SerpApi credit was used; keep private unless more proof is found."


def home_depot_proof_lines(candidate: SourceCandidate, reasons: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    attrs = candidate.variant_attributes or {}
    for attr_key, label in (
        ("price_badge", "Badge"),
        ("percentage_off", "Percent off"),
        ("price_saving", "Savings"),
        ("brand", "Brand"),
        ("rating", "Rating"),
        ("reviews", "Reviews"),
    ):
        if attrs.get(attr_key):
            lines.append(f"• {label}: {attrs[attr_key]}")
    for reason in reasons[:3]:
        if len(lines) >= 6:
            break
        lines.append(f"• {reason}")
    for signal in candidate.signals[:4]:
        if len(lines) >= 6:
            break
        lines.append(f"• {signal}")
    if not lines:
        lines.append("• Home Depot product link returned by SerpApi")
    return lines


def home_depot_heat_emoji(current_price: float | None, penny_score: int) -> str:
    if current_price is not None and price_ending(current_price) == "01":
        return "🚨"
    if penny_score >= 80:
        return "🚨"
    if penny_score >= 60:
        return "🔥"
    if penny_score >= 30:
        return "💎"
    return "✅"


def home_depot_label(penny_score: int) -> str:
    if penny_score >= 80:
        return "HIGH-PRIORITY VERIFY"
    if penny_score >= 60:
        return "STRONG CLEARANCE LEAD"
    if penny_score >= 30:
        return "CLEARANCE WATCH"
    return "HOME DEPOT LEAD"


def home_depot_color(penny_score: int) -> discord.Color:
    if penny_score >= 80:
        return discord.Color.red()
    if penny_score >= 60:
        return discord.Color.orange()
    return discord.Color.gold()


def friendly_penny_level(level: str) -> str:
    labels = {
        "high_priority_in_store_verification": "High-priority verify",
        "strong_penny_candidate": "Strong penny candidate",
        "clearance_watch": "Clearance watch",
        "weak_lead": "Weak lead",
    }
    return labels.get(level, level.replace("_", " ").title())


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def price_ending(price: float | None) -> str | None:
    if price is None:
        return None
    cents = int(round((price - int(price)) * 100))
    if cents < 0 or cents > 99:
        return None
    return f"{cents:02d}"


def trim_title(title: str, limit: int) -> str:
    cleaned = " ".join(title.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _scan_failed_before_success(result: ProviderScanResult) -> bool:
    if result.candidates:
        return False
    failure_prefixes = (
        "SerpApi error",
        "SerpApi HTTP",
        "SerpApi network",
        "SerpApi returned",
    )
    return any(warning.startswith(failure_prefixes) for warning in result.warnings)


def _broad_query_warning(query: str) -> str | None:
    if query.strip().lower() in {"clearance", "sale", "tools", "home depot", "deal", "deals"}:
        return "This is a broad query and can waste credits. Use tighter terms like `milwaukee drill`, `faucet`, `vanity`, or `ceiling fan`."
    return None
