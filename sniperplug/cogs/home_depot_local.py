from __future__ import annotations

import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.penny_score import score_penny_candidate
from sniperplug.services.quota_guard import serpapi_quota_guard
from sniperplug.cogs.home_depot_search import home_depot_link_block, money, price_ending, trim_title


ZIP_RE = re.compile(r"^\d{5}$")
SKU_RE = re.compile(r"^[A-Za-z0-9-]{4,24}$")
ID_TOKEN_RE = re.compile(r"[A-Za-z0-9-]{4,}")


@dataclass(frozen=True)
class HomeDepotLocalScan:
    sku: str
    zip_code: str
    query: str
    candidates: tuple[SourceCandidate, ...]
    warnings: tuple[str, ...]
    quota_text: str
    returned_count: int = 0
    returned_candidates: tuple[SourceCandidate, ...] = ()

    @property
    def best_candidate(self) -> SourceCandidate | None:
        return self.candidates[0] if self.candidates else None


class HomeDepotLocalCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hd_stock", description="Check a Home Depot SKU near a ZIP and show local stock/price proof candidates.")
    @app_commands.describe(
        sku="Home Depot SKU / Internet #, like 334851114.",
        zip_code="5-digit ZIP code to anchor the local Home Depot check.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_stock(self, interaction: discord.Interaction, sku: str, zip_code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        cleaned_sku, cleaned_zip = _clean_sku(sku), _clean_zip(zip_code)
        error = _validation_error(cleaned_sku, cleaned_zip)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        scan = await run_home_depot_local_scan(interaction.user.id, cleaned_sku, cleaned_zip)
        await interaction.followup.send(embed=build_hd_stock_embed(scan), ephemeral=True)

    @app_commands.command(name="hd_penny_zip", description="Start a ZIP-anchored Home Depot penny/clearance scan using a safe default search.")
    @app_commands.describe(zip_code="5-digit ZIP code to anchor the penny/clearance scan.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_penny_zip(self, interaction: discord.Interaction, zip_code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        cleaned_zip = _clean_zip(zip_code)
        if not ZIP_RE.match(cleaned_zip):
            await interaction.followup.send("Please enter a valid 5-digit ZIP code.", ephemeral=True)
            return

        provider = provider_registry.get("home_depot_serpapi")
        if provider is None:
            await interaction.followup.send("Home Depot SerpApi provider is not registered yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(health.message, ephemeral=True)
            return

        quota = serpapi_quota_guard.check(interaction.user.id, cost=1)
        if not quota.allowed:
            await interaction.followup.send(f"SerpApi scan blocked: {quota.reason}", ephemeral=True)
            return

        query = "clearance"
        result = await provider.scan(
            ProviderScanRequest(
                source_key="home_depot_serpapi",
                query=query,
                max_results=24,
                metadata={"zip_code": cleaned_zip, "requested_by": str(interaction.user.id), "scan_type": "hd_penny_zip"},
            )
        )
        quota_after = serpapi_quota_guard.record(interaction.user.id, cost=1)
        candidates = tuple(sorted(result.candidates, key=_penny_sort_key, reverse=True))[:8]
        embed = build_hd_penny_zip_embed(cleaned_zip, candidates, result.warnings, quota_after.daily_used, quota_after.daily_limit)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def run_home_depot_local_scan(user_id: int, sku: str, zip_code: str) -> HomeDepotLocalScan:
    provider = provider_registry.get("home_depot_serpapi")
    if provider is None:
        return HomeDepotLocalScan(sku, zip_code, sku, (), ("Home Depot SerpApi provider is not registered yet.",), "SerpApi unavailable")

    health = await provider.healthcheck()
    if health.status != ProviderStatus.READY:
        return HomeDepotLocalScan(sku, zip_code, sku, (), (health.message,), "SerpApi unavailable")

    quota = serpapi_quota_guard.check(user_id, cost=1)
    if not quota.allowed:
        return HomeDepotLocalScan(sku, zip_code, sku, (), (f"SerpApi scan blocked: {quota.reason}",), "SerpApi blocked")

    result = await provider.scan(
        ProviderScanRequest(
            source_key="home_depot_serpapi",
            query=sku,
            max_results=24,
            metadata={"zip_code": zip_code, "requested_by": str(user_id), "scan_type": "hd_stock"},
        )
    )
    quota_after = serpapi_quota_guard.record(user_id, cost=1)
    candidates = _exact_sku_candidates(result.candidates, sku)
    quota_text = f"SerpApi used: {quota_after.daily_used}/{quota_after.daily_limit} today"
    return HomeDepotLocalScan(
        sku,
        zip_code,
        sku,
        candidates,
        result.warnings,
        quota_text,
        returned_count=len(result.candidates),
        returned_candidates=tuple(result.candidates),
    )


def build_hd_stock_embed(scan: HomeDepotLocalScan) -> discord.Embed:
    candidate = scan.best_candidate
    embed = discord.Embed(
        title=f"🏚️ Home Depot Stock Check • SKU {scan.sku}",
        description=(
            f"ZIP: `{scan.zip_code}`\n"
            "Hard rule: SniperPlug only shows a stock card when the returned product ID/SKU/URL exactly matches your requested SKU."
        ),
        color=discord.Color.orange(),
    )

    if not candidate:
        embed.add_field(
            name="No exact SKU proof returned",
            value=(
                f"SerpApi/Home Depot returned `{scan.returned_count}` product result(s), but none exactly matched requested SKU `{scan.sku}`.\n"
                "SniperPlug blocked the card instead of showing a possibly wrong product. The closest returned result is shown below for staff review only."
            ),
            inline=False,
        )
        closest = scan.returned_candidates[0] if scan.returned_candidates else None
        if closest:
            if closest.image_url:
                embed.set_thumbnail(url=closest.image_url)
            embed.add_field(
                name="Closest returned result",
                value=(
                    f"**{trim_title(closest.title, 90)}**\n"
                    f"Returned ID/SKU: `{closest.sku or closest.product_id or 'n/a'}`\n"
                    f"Price: **{money(closest.current_price)}**\n"
                    f"Stock: **{closest.stock_status or 'not returned'}**"
                ),
                inline=False,
            )
            embed.add_field(name="Review link", value=home_depot_link_block(closest), inline=False)
        if scan.warnings:
            embed.add_field(name="Provider notes", value="\n".join(f"• {w}" for w in scan.warnings[:5]), inline=False)
        embed.set_footer(text=f"{scan.quota_text} • Blocked because exact proof was missing.")
        return embed

    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)
    embed.url = candidate.product_url
    embed.add_field(name="Product", value=f"**{trim_title(candidate.title, 120)}**\nSKU/ID: `{candidate.sku or candidate.product_id or scan.sku}`", inline=False)
    embed.add_field(name="Price", value=_local_price_block(candidate), inline=True)
    embed.add_field(name="Stock / fulfillment", value=_local_stock_block(candidate), inline=True)
    embed.add_field(name="Links", value=home_depot_link_block(candidate), inline=False)

    penny = score_penny_candidate(candidate, has_store_id=True)
    embed.add_field(
        name="SniperPlug read",
        value=(
            f"Score: `{penny.score}/100`\n"
            f"Level: **{penny.level.replace('_', ' ').title()}**\n"
            "Public alert: **No — staff verification first**"
        ),
        inline=False,
    )
    reasons = [f"Exact SKU/Product ID/URL match for `{scan.sku}`"] + list(penny.reasons[:3]) + [s for s in candidate.signals[:5] if s]
    if reasons:
        embed.add_field(name="Why it showed up", value="\n".join(f"• {reason}" for reason in reasons[:6]), inline=False)
    if scan.warnings:
        embed.add_field(name="Provider notes", value="\n".join(f"• {w}" for w in scan.warnings[:5]), inline=False)
    embed.set_footer(text=f"{scan.quota_text} • Exact SKU/URL match required. Local inventory may be stale. Call/check before driving.")
    return embed


def build_hd_penny_zip_embed(zip_code: str, candidates: tuple[SourceCandidate, ...], warnings: tuple[str, ...], used: int, limit: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"🟡 Home Depot Penny / Clearance ZIP Scan • {zip_code}",
        description=(
            "V1 scans a targeted Home Depot clearance query with ZIP context and ranks returned candidates by penny/clearance signals. "
            "This is **not** a locked ZIP penny database yet."
        ),
        color=discord.Color.gold(),
    )
    if not candidates:
        embed.add_field(name="No candidates", value="No Home Depot products came back for the ZIP scan. Try `/home_depot_penny_hunt` with a tighter query like `faucet`, `vanity`, `ryobi`, or `milwaukee`.", inline=False)
    else:
        lines: list[str] = []
        for idx, candidate in enumerate(candidates[:6], start=1):
            score = score_penny_candidate(candidate, has_store_id=True).score
            lines.append(
                f"**{idx}. {trim_title(candidate.title, 55)}**\n"
                f"Price: **{money(candidate.current_price)}** • Score: `{score}/100` • SKU: `{candidate.sku or candidate.product_id or 'n/a'}`"
            )
        embed.add_field(name="Top candidates", value="\n\n".join(lines), inline=False)
    if warnings:
        embed.add_field(name="Provider notes", value="\n".join(f"• {w}" for w in warnings[:5]), inline=False)
    embed.set_footer(text=f"SerpApi used: {used}/{limit} today • Verify in store before posting.")
    return embed


def _local_price_block(candidate: SourceCandidate) -> str:
    ending = price_ending(candidate.current_price)
    ending_text = f"\nEnding: **.{ending}**" if ending else ""
    if candidate.typical_price and candidate.current_price and candidate.typical_price > candidate.current_price:
        savings = candidate.typical_price - candidate.current_price
        pct = savings / candidate.typical_price * 100
        return f"Now: **{money(candidate.current_price)}**\nWas: **{money(candidate.typical_price)}**\nSave: **{money(savings)} ({pct:.0f}%)**{ending_text}"
    return f"Now: **{money(candidate.current_price)}**\nWas: **Not returned**{ending_text}"


def _local_stock_block(candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    lines: list[str] = []
    if candidate.stock_status:
        lines.append(candidate.stock_status)
    for key in ("store_stock", "store_stock_status", "pickup", "delivery", "general_stock", "general_stock_status"):
        value = attrs.get(key)
        if value:
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
    return "\n".join(lines[:5]) if lines else "Local stock not returned"


def _exact_sku_candidates(candidates: tuple[SourceCandidate, ...], sku: str) -> tuple[SourceCandidate, ...]:
    normalized = _normalize_id(sku)
    exact = [candidate for candidate in candidates if _is_exact_sku_match(candidate, normalized)]
    return tuple(sorted(exact, key=lambda c: score_penny_candidate(c, has_store_id=True).score, reverse=True))


def _is_exact_sku_match(candidate: SourceCandidate, normalized_sku: str) -> bool:
    if not normalized_sku:
        return False
    return normalized_sku in _candidate_match_ids(candidate)


def _candidate_match_ids(candidate: SourceCandidate) -> set[str]:
    ids = {_normalize_id(v) for v in (candidate.sku, candidate.product_id, candidate.upc, candidate.selected_offer_id) if v}
    for token in ID_TOKEN_RE.findall(candidate.product_url or ""):
        normalized = _normalize_id(token)
        if normalized:
            ids.add(normalized)
    return ids


def _penny_sort_key(candidate: SourceCandidate) -> tuple[int, int]:
    score = score_penny_candidate(candidate, has_store_id=True).score
    price_bonus = 100 - int(candidate.current_price or 99)
    return score, price_bonus


def _clean_sku(value: str) -> str:
    return "".join(value.strip().split())


def _clean_zip(value: str) -> str:
    return value.strip()


def _validation_error(sku: str, zip_code: str) -> str | None:
    if not ZIP_RE.match(zip_code):
        return "Please enter a valid 5-digit ZIP code."
    if not SKU_RE.match(sku):
        return "Please enter a valid Home Depot SKU / Internet # using 4-24 letters or numbers."
    return None


def _normalize_id(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())
