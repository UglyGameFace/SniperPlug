from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.penny_score import score_penny_candidate
from sniperplug.services.quota_guard import serpapi_quota_guard
from sniperplug.services.safe_links import product_link_choices


@dataclass(frozen=True)
class HomeDepotCardBatch:
    embeds: list[discord.Embed]
    candidates: list[SourceCandidate]
    returned_count: int
    shown_count: int
    filtered_count: int
    used_raw_fallback: bool


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
        store_id="Optional Home Depot store ID. ZIP alone is enough for a local search.",
        zip_code="Recommended local ZIP. Used as the local anchor when store ID is unknown.",
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

        cleaned_store_id = (store_id or "").strip()
        cleaned_zip_code = (zip_code or "").strip()
        has_local_anchor = bool(cleaned_store_id or cleaned_zip_code)
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
                    "store_id": cleaned_store_id,
                    "zip_code": cleaned_zip_code,
                    "requested_by": str(interaction.user.id),
                },
            )
        )
        quota_after = serpapi_quota_guard.record(interaction.user.id, cost=1)

        if result.warnings and not result.candidates:
            await interaction.followup.send("\n".join(result.warnings), ephemeral=True)
            return

        batch = build_home_depot_card_batch(result.candidates, has_local_anchor=has_local_anchor, penny_mode=penny_mode)
        summary = discord.Embed(
            title="🏚️ Home Depot Penny Hunt" if penny_mode else "🏚️ Home Depot Search",
            description=(
                f"Searching: **{query}**\n"
                f"Store: `{cleaned_store_id or 'n/a'}` • ZIP: `{cleaned_zip_code or 'n/a'}` • Page: `{page}`\n"
                f"SerpApi used: **{quota_after.monthly_used}/{quota_after.monthly_limit} monthly safe budget** • "
                f"**{quota_after.daily_used}/{quota_after.daily_limit} today**\n"
                f"Products returned: **{batch.returned_count}** • Cards shown: **{batch.shown_count}**"
            ),
            color=discord.Color.orange(),
        )
        if penny_mode:
            summary.description += f" • Penny-filtered: **{batch.filtered_count}**"
        summary.description += "\nThese are **verification candidates**, not confirmed in-store penny deals."
        if batch.used_raw_fallback:
            summary.add_field(
                name="Credit protected",
                value="SerpApi returned products, but none passed the penny threshold. Showing raw low-score results anyway so the credit is not wasted.",
                inline=False,
            )
        if batch.candidates:
            summary.add_field(
                name="Link choices",
                value="Use **Open App/Web** if your phone/tablet supports the retailer app. Use **Browser Search** if the app handoff breaks or your device is unsupported.",
                inline=False,
            )
        if cleaned_zip_code and not cleaned_store_id:
            summary.add_field(
                name="ZIP used as local anchor",
                value="No store ID needed. SniperPlug used the ZIP for local Home Depot search context; store-specific proof is still stronger when available.",
                inline=False,
            )
        elif not has_local_anchor:
            summary.add_field(
                name="Local proof warning",
                value="No ZIP or store ID was supplied, so scores are weaker and local stock proof is limited.",
                inline=False,
            )
        if broad_warning:
            summary.add_field(name="⚠️ Quota warning", value=broad_warning, inline=False)
        if result.warnings:
            summary.add_field(name="⚠️ Provider notes", value="\n".join(result.warnings[:3]), inline=False)
        if not batch.embeds:
            summary.add_field(
                name="No products returned",
                value="SerpApi did not return product cards for this query/location. Try a different query, ZIP, or page.",
                inline=False,
            )
            await interaction.followup.send(embed=summary, ephemeral=True)
            return

        await interaction.followup.send(
            embeds=[summary] + batch.embeds[:5],
            view=HomeDepotResultView(batch.candidates[:5]),
            ephemeral=True,
        )


def build_home_depot_cards(candidates: tuple[SourceCandidate, ...], *, has_store_id: bool, penny_mode: bool) -> list[discord.Embed]:
    return build_home_depot_card_batch(candidates, has_local_anchor=has_store_id, penny_mode=penny_mode).embeds


class HomeDepotResultView(discord.ui.View):
    def __init__(self, candidates: list[SourceCandidate]):
        super().__init__(timeout=300)
        for idx, candidate in enumerate(candidates, start=1):
            choices = product_link_choices(
                retailer=candidate.retailer,
                product_url=candidate.product_url,
                title=candidate.title,
                product_id=candidate.product_id,
                sku=candidate.sku,
                asin=candidate.product_id if candidate.product_id_type == "asin" else None,
            )
            row = (idx - 1) // 2
            for choice in choices[:2]:
                self.add_item(discord.ui.Button(label=f"{idx} {choice.label}", style=discord.ButtonStyle.link, url=choice.url, row=row))


def build_home_depot_card_batch(candidates: tuple[SourceCandidate, ...], *, has_local_anchor: bool, penny_mode: bool) -> HomeDepotCardBatch:
    scored: list[tuple[int, discord.Embed, SourceCandidate]] = []
    filtered_count = 0
    for candidate in candidates:
        penny = score_penny_candidate(candidate, has_store_id=has_local_anchor)
        if penny_mode and penny.score < 25:
            filtered_count += 1
            continue
        embed = build_home_depot_deal_card(candidate, penny.score, penny.level, penny.reasons, raw_fallback=False)
        scored.append((penny.score, embed, candidate))

    used_raw_fallback = False
    if penny_mode and not scored and candidates:
        used_raw_fallback = True
        filtered_count = len(candidates)
        for candidate in candidates:
            penny = score_penny_candidate(candidate, has_store_id=has_local_anchor)
            embed = build_home_depot_deal_card(candidate, penny.score, penny.level, penny.reasons, raw_fallback=True)
            scored.append((penny.score, embed, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    embeds = [embed for _, embed, _ in scored]
    shown_candidates = [candidate for _, _, candidate in scored]
    return HomeDepotCardBatch(
        embeds=embeds,
        candidates=shown_candidates,
        returned_count=len(candidates),
        shown_count=min(len(embeds), 5),
        filtered_count=filtered_count if penny_mode else 0,
        used_raw_fallback=used_raw_fallback,
    )


def build_home_depot_deal_card(
    candidate: SourceCandidate,
    penny_score: int,
    penny_level: str,
    reasons: tuple[str, ...],
    *,
    raw_fallback: bool = False,
) -> discord.Embed:
    label = "RAW SERPAPI RESULT" if raw_fallback else home_depot_label(penny_score)
    title = f"{home_depot_heat_emoji(candidate.current_price, penny_score)} {label} • {trim_title(candidate.title, 72)}"
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

    proof_block = home_depot_product_proof_block(candidate)
    if proof_block:
        embed.add_field(name="🧾 Product Proof", value=proof_block, inline=False)

    fulfillment_block = home_depot_fulfillment_block(candidate)
    if fulfillment_block:
        embed.add_field(name="🚚 Fulfillment", value=fulfillment_block, inline=False)

    embed.add_field(name="🟢 Liveness", value=home_depot_liveness_block(candidate.current_price, penny_score, raw_fallback=raw_fallback), inline=False)

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
    attrs = candidate.variant_attributes or {}
    if current_price is None:
        return "Current price: **Unavailable**\nHome Depot/SerpApi did not return a current price for this result."
    ending = price_ending(current_price)
    ending_line = f"\nEnding: **.{ending}**" if ending else ""
    badge_line = f"\nBadge: **{attrs['price_badge']}**" if attrs.get("price_badge") else ""
    if candidate.typical_price and candidate.typical_price > current_price:
        savings = candidate.typical_price - current_price
        discount = (savings / candidate.typical_price) * 100
        return (
            f"**{money(current_price)}**\n"
            f"Was/typical: **{money(candidate.typical_price)}**\n"
            f"Save: **{money(savings)} ({discount:.0f}%)**{ending_line}{badge_line}"
        )
    savings_text = attrs.get("price_saving")
    percent_text = attrs.get("percentage_off")
    if savings_text or percent_text:
        return (
            f"**{money(current_price)}**\n"
            "Was/typical: **Unavailable from SerpApi**\n"
            f"Home Depot savings text: **{savings_text or 'n/a'} {f'({percent_text})' if percent_text else ''}**{ending_line}{badge_line}"
        )
    return f"**{money(current_price)}**\nWas/typical: **Unavailable from SerpApi**\nSavings: **Not calculable from returned data**{ending_line}{badge_line}"


def home_depot_product_proof_block(candidate: SourceCandidate) -> str | None:
    attrs = candidate.variant_attributes or {}
    lines: list[str] = []
    for key, label in (
        ("brand", "Brand"),
        ("model_number", "Model"),
        ("rating", "Rating"),
        ("reviews", "Reviews"),
        ("badges", "Badges"),
        ("collection", "Collection"),
    ):
        value = attrs.get(key)
        if value:
            lines.append(f"{label}: **{value}**")
    if candidate.can_add_to_cart is not None:
        lines.append(f"Add-to-cart: **{'seen' if candidate.can_add_to_cart else 'not seen'}**")
    return "\n".join(lines[:7]) if lines else None


def home_depot_fulfillment_block(candidate: SourceCandidate) -> str | None:
    attrs = candidate.variant_attributes or {}
    lines = []
    for key in ("pickup", "delivery", "general_stock", "general_stock_status", "store_stock", "store_stock_status"):
        value = attrs.get(key)
        if value:
            lines.append(f"{key.replace('_', ' ').title()}: **{value}**")
    return "\n".join(lines[:6]) if lines else None


def home_depot_stock_block(candidate: SourceCandidate) -> str:
    lines: list[str] = []
    if candidate.stock_status:
        lines.append(candidate.stock_status[:120])
    for signal in candidate.signals:
        if signal.startswith("store_id:") or signal.startswith("zip:"):
            lines.append(signal)
    return "\n".join(lines) if lines else "Local stock not confirmed"


def home_depot_liveness_block(current_price: float | None, penny_score: int, *, raw_fallback: bool = False) -> str:
    if raw_fallback:
        return "⚪ **Raw low-score result.** Shown because SerpApi returned products and SniperPlug will not hide paid-credit results."
    if current_price is not None and price_ending(current_price) == "01":
        return "🚨 **Possible penny candidate.** SerpApi is not register proof; verify with in-store scan/register before posting."
    if penny_score >= 80:
        return "🔥 **High-priority verification candidate.** Do not public-alert until in-store proof confirms it."
    if penny_score >= 60:
        return "💎 **Strong clearance candidate.** Send to staff review and verify locally."
    if penny_score >= 40:
        return "✅ **Clearance watch.** Has multiple clearance/deal signals, but still needs local verification."
    if penny_score >= 20:
        return "🟡 **Deal watch.** Possible useful deal, but not enough proof to call clearance or penny."
    return "⚪ **Weak lead.** Keep private unless more proof is found."


def home_depot_proof_lines(candidate: SourceCandidate, reasons: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    attrs = candidate.variant_attributes or {}
    for reason in reasons[:3]:
        lines.append(f"• {reason}")
    for key, label in (
        ("price_badge", "badge"),
        ("price_saving", "savings"),
        ("percentage_off", "percent off"),
    ):
        value = attrs.get(key)
        if value and len(lines) < 6:
            lines.append(f"• Home Depot {label}: {value}")
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
    if penny_score >= 40:
        return "💎"
    if penny_score >= 20:
        return "🟡"
    return "✅"


def home_depot_label(penny_score: int) -> str:
    if penny_score >= 80:
        return "HIGH-PRIORITY VERIFY"
    if penny_score >= 60:
        return "STRONG CLEARANCE LEAD"
    if penny_score >= 40:
        return "CLEARANCE WATCH"
    if penny_score >= 20:
        return "DEAL WATCH"
    return "HOME DEPOT LEAD"


def home_depot_color(penny_score: int) -> discord.Color:
    if penny_score >= 80:
        return discord.Color.red()
    if penny_score >= 60:
        return discord.Color.orange()
    if penny_score >= 40:
        return discord.Color.gold()
    return discord.Color.light_grey()


def friendly_penny_level(level: str) -> str:
    labels = {
        "high_priority_in_store_verification": "High-priority verify",
        "strong_clearance_candidate": "Strong clearance candidate",
        "strong_penny_candidate": "Strong penny candidate",
        "clearance_watch": "Clearance watch",
        "deal_watch": "Deal watch",
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


def _broad_query_warning(query: str) -> str | None:
    if query.strip().lower() in {"clearance", "sale", "tools", "home depot", "deal", "deals"}:
        return "This is a broad query and can waste credits. Use tighter terms like `milwaukee drill`, `faucet`, `vanity`, or `ceiling fan`."
    return None
