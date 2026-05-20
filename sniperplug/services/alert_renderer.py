from __future__ import annotations

import discord

from sniperplug.models.deal import NormalizedDeal


SHORT_DISCLAIMER = "Verify final checkout price, seller, condition, shipping, and returns."
AMAZON_DISCLAIMER = (
    "Amazon deals can be account-specific, ZIP-based, Prime-only, seller-specific, "
    "canceled, gone, or different by checkout."
)


def money(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"${value:,.2f}"


def percent(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"{value:.0f}%"


def build_deal_embed(deal: NormalizedDeal) -> discord.Embed:
    title_prefix = " • ".join(deal.alert_tags) if deal.alert_tags else "🔎 Deal Alert"

    embed = discord.Embed(
        title=title_prefix,
        description=f"**{deal.title}**",
        color=discord.Color.orange(),
        url=deal.product_url,
    )

    if deal.image_url:
        embed.set_image(url=deal.image_url)

    price_line = (
        f"**Now:** {money(deal.current_price)}\n"
        f"**Typical:** {money(deal.typical_price)}\n"
        f"**Save:** {money(deal.savings_amount)} ({percent(deal.discount_percent)})\n"
        f"**Risk:** {deal.risk_level.title()}"
    )
    embed.add_field(name="Deal Snapshot", value=price_line, inline=False)

    seller_bits: list[str] = []
    if deal.retailer:
        seller_bits.append(f"Retailer: `{deal.retailer}`")
    if deal.seller_name:
        seller_bits.append(f"Seller: `{deal.seller_name}`")
    if deal.fulfillment_type:
        seller_bits.append(f"Fulfillment: `{deal.fulfillment_type}`")
    elif deal.fulfilled_by_amazon is not None:
        seller_bits.append("Fulfilled by Amazon: `" + ("Yes" if deal.fulfilled_by_amazon else "No / Unknown") + "`")
    if deal.condition:
        seller_bits.append(f"Condition: `{deal.condition}`")

    if seller_bits:
        embed.add_field(name="Store / Seller", value="\n".join(seller_bits[:4]), inline=False)

    compact_flags = build_compact_flags(deal)
    if compact_flags:
        embed.add_field(name="Why flagged", value="\n".join(f"• {flag}" for flag in compact_flags[:5]), inline=False)

    warning = AMAZON_DISCLAIMER if deal.retailer.lower() == "amazon" else SHORT_DISCLAIMER
    embed.add_field(name="SniperPlug warning", value=warning, inline=False)

    identifiers: list[str] = []
    if deal.asin:
        identifiers.append(f"ASIN: `{deal.asin}`")
    if deal.sku:
        identifiers.append(f"SKU: `{deal.sku}`")
    if deal.upc:
        identifiers.append(f"UPC: `{deal.upc}`")
    if identifiers:
        embed.add_field(name="IDs", value=" • ".join(identifiers), inline=False)

    embed.set_footer(text=f"SniperPlug • Confidence {deal.confidence_score}/100 • {SHORT_DISCLAIMER}")
    return embed


def build_compact_flags(deal: NormalizedDeal) -> list[str]:
    flags: list[str] = []

    if deal.discount_percent is not None:
        flags.append(f"{deal.discount_percent:.0f}% below typical price")

    if deal.is_possible_price_error:
        flags.append("Possible price error or fast-moving glitch")

    if deal.is_ymmv:
        flags.append("YMMV: may not appear for everyone")

    if deal.fulfilled_by_amazon is False:
        flags.append("Merchant fulfilled")

    if deal.seller_name and deal.seller_name.lower() not in {"amazon", "amazon.com"}:
        flags.append(f"Third-party seller: {deal.seller_name}")

    if deal.condition:
        condition = deal.condition.lower()
        if any(word in condition for word in ["renewed", "used", "open box", "refurbished"]):
            flags.append(f"Condition: {deal.condition}")

    if not flags and deal.risk_flags:
        flags.extend(deal.risk_flags[:3])

    return unique_keep_order(flags)


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output


class DealActionView(discord.ui.View):
    def __init__(self, db, deal: NormalizedDeal):
        super().__init__(timeout=3600)
        self.db = db
        self.deal = deal

        existing_buttons = list(self.children)
        self.clear_items()

        self.add_item(
            discord.ui.Button(
                label="View Deal",
                style=discord.ButtonStyle.link,
                url=deal.product_url,
                emoji="🛒",
            )
        )

        for button in existing_buttons:
            self.add_item(button)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.secondary, emoji="🔖")
    async def save_deal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.db.save_deal(
            guild_id=interaction.guild_id or 0,
            user_id=interaction.user.id,
            deal_id=self.deal.deal_id,
        )
        await interaction.response.send_message("Saved this deal to your SniperPlug list.", ephemeral=True)

    @discord.ui.button(label="Report Dead", style=discord.ButtonStyle.danger, emoji="🪦")
    async def report_dead(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.db.report_dead(
            guild_id=interaction.guild_id or 0,
            user_id=interaction.user.id,
            deal_id=self.deal.deal_id,
        )
        await interaction.response.send_message(
            "Dead deal report received. Good looks — this helps keep SniperPlug clean.",
            ephemeral=True,
        )
