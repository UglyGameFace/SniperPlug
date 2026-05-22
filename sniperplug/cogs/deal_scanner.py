from __future__ import annotations

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


class DealScannerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="walmart_scan", description="Search Walmart deals with filters, page controls, and View Deal buttons.")
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

        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("Walmart provider is not registered yet.", ephemeral=True)
            return

        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(f"Walmart is not ready: {health.message}", ephemeral=True)
            return

        sort_value, order_value = parse_sort_choice(sort.value if sort else None)
        request = ProviderScanRequest(
            source_key="walmart",
            query=query.strip(),
            max_results=max_results,
            page=page,
            sort=sort_value,
            order=order_value,
            metadata={"requested_by": str(interaction.user.id)},
        )
        result = await provider.scan(request)

        cards = build_walmart_cards(result, min_discount=min_discount, alerts_only=alerts_only)
        summary = build_scan_summary(result, query=query, min_discount=min_discount, alerts_only=alerts_only)

        if not cards:
            summary.add_field(
                name="No matching deals on this page",
                value=(
                    "Try one of these next:\n"
                    f"• `/walmart_scan query:{query} min_discount:50 page:{page + 1}`\n"
                    f"• `/walmart_scan query:{query} min_discount:0 page:{page}`\n"
                    "• Try a tighter product term like `oled tv`, `gaming monitor`, `lego`, `patio`, `ssd`."
                ),
                inline=False,
            )
            await interaction.followup.send(embed=summary, ephemeral=True)
            return

        embeds = [summary] + [card.embed for card in cards[:5]]
        view = DealButtonView(cards[:5], has_next_page=result.has_next_page, page=page, query=query, min_discount=min_discount)
        await interaction.followup.send(embeds=embeds, view=view, ephemeral=True)

    @walmart_scan.error
    async def walmart_scan_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to run Walmart scans."
        else:
            message = f"Walmart scan hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class DealCard:
    def __init__(self, embed: discord.Embed, url: str, label: str):
        self.embed = embed
        self.url = url
        self.label = label


class DealButtonView(discord.ui.View):
    def __init__(self, cards: list[DealCard], has_next_page: bool, page: int, query: str, min_discount: int):
        super().__init__(timeout=300)
        for idx, card in enumerate(cards, start=1):
            self.add_item(discord.ui.Button(label=f"View Deal {idx}", style=discord.ButtonStyle.link, url=card.url))

        if has_next_page:
            hint = f"Run /walmart_scan query:{query} min_discount:{min_discount} page:{page + 1}"
        else:
            hint = "No next page reported by provider. Try a different query or lower min_discount."
        self.add_item(discord.ui.Button(label="Next page hint", style=discord.ButtonStyle.secondary, disabled=True, custom_id="next_page_hint"))
        self.next_page_hint = hint


def build_scan_summary(result: ProviderScanResult, query: str, min_discount: int, alerts_only: bool) -> discord.Embed:
    total = f"{result.total_results:,}" if result.total_results is not None else "unknown"
    page_size = result.page_size or len(result.candidates)
    start = result.start_index or ((result.page - 1) * page_size + 1)
    end = start + max(len(result.candidates) - 1, 0)
    next_text = f"Yes — try page `{result.page + 1}`" if result.has_next_page else "Not reported"

    embed = discord.Embed(
        title="🛒 Walmart Deal Scanner",
        description=(
            f"Query: `{query}`\n"
            f"Filter: **{min_discount}%+ off**{' • alerts only' if alerts_only else ''}\n"
            f"Page: **{result.page}** • Showing raw results **{start}-{end}** of **{total}**\n"
            f"Next page: **{next_text}**"
        ),
        color=discord.Color.orange(),
    )
    if result.warnings:
        embed.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in result.warnings[:3]), inline=False)
    embed.set_footer(text="Prices can revert. Re-run this scan before posting or buying. Local/in-store stock needs separate local stock checks.")
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
        cards.append(DealCard(embed=embed, url=deal.product_url, label=short_button_label(deal.title)))
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
