from __future__ import annotations

import discord

from sniperplug.models.deal import NormalizedDeal


DISCLAIMER = (
    "Some deals may be price errors, glitches, account-specific, regional, Prime-only, "
    "seller-specific, canceled, sold out, or different by checkout. Always verify final price, "
    "seller, condition, shipping, and return policy."
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
    title_prefix = " | ".join(deal.alert_tags) if deal.alert_tags else "🔎 Deal Alert"

    embed = discord.Embed(
        title=f"{title_prefix}",
        description=f"**{deal.title}**",
        color=discord.Color.orange(),
        url=deal.product_url,
    )

    if deal.image_url:
        embed.set_image(url=deal.image_url)

    embed.add_field(name="Retailer", value=deal.retailer, inline=True)
    embed.add_field(name="Price", value=money(deal.current_price), inline=True)
    embed.add_field(name="Typical", value=money(deal.typical_price), inline=True)

    embed.add_field(name="Discount", value=percent(deal.discount_percent), inline=True)
    embed.add_field(name="Savings", value=money(deal.savings_amount), inline=True)
    embed.add_field(name="Risk", value=deal.risk_level.title(), inline=True)

    seller_bits: list[str] = []
    if deal.seller_name:
        seller_bits.append(f"Seller: `{deal.seller_name}`")
    if deal.fulfillment_type:
        seller_bits.append(f"Fulfillment: `{deal.fulfillment_type}`")
    elif deal.fulfilled_by_amazon is not None:
        seller_bits.append("Fulfilled by Amazon: `" + ("Yes" if deal.fulfilled_by_amazon else "No / Unknown") + "`")
    if deal.condition:
        seller_bits.append(f"Condition: `{deal.condition}`")

    if seller_bits:
        embed.add_field(name="Seller / Condition", value="\n".join(seller_bits), inline=False)

    if deal.risk_flags:
        flags = "\n".join(f"• {flag}" for flag in deal.risk_flags[:6])
        embed.add_field(name="Heads up", value=flags, inline=False)

    identifiers: list[str] = []
    if deal.asin:
        identifiers.append(f"ASIN: `{deal.asin}`")
    if deal.sku:
        identifiers.append(f"SKU: `{deal.sku}`")
    if deal.upc:
        identifiers.append(f"UPC: `{deal.upc}`")
    if identifiers:
        embed.add_field(name="IDs", value="\n".join(identifiers), inline=False)

    embed.set_footer(text=f"SniperPlug • Confidence {deal.confidence_score}/100 • {DISCLAIMER}")
    return embed


class DealActionView(discord.ui.View):
    def __init__(self, db, deal: NormalizedDeal):
        super().__init__(timeout=3600)
        self.db = db
        self.deal = deal

        self.add_item(
            discord.ui.Button(
                label="View Deal",
                style=discord.ButtonStyle.link,
                url=deal.product_url,
                emoji="🛒",
            )
        )

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
