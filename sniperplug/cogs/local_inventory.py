from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.local_inventory import LocalInventoryProof, LocalInventoryRequest
from sniperplug.providers.registry import provider_registry


RETAILER_ALIASES = {
    "home": "home_depot",
    "home depot": "home_depot",
    "homedepot": "home_depot",
    "hd": "home_depot",
    "the home depot": "home_depot",
    "walmart": "walmart",
    "wal mart": "walmart",
    "bestbuy": "bestbuy",
    "best buy": "bestbuy",
    "bb": "bestbuy",
}


class LocalInventoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="local_check", description="Create a safe local inventory proof check without public posting.")
    @app_commands.describe(
        retailer="Retailer key, like home_depot.",
        sku="Optional store SKU / item ID / internet number.",
        zip_code="Optional ZIP code to anchor the local check.",
        store_id="Optional store ID if known.",
        observed_price="Optional locally observed price.",
        upc="Optional UPC if known.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def local_check(
        self,
        interaction: discord.Interaction,
        retailer: str,
        sku: str | None = None,
        zip_code: str | None = None,
        store_id: str | None = None,
        observed_price: float | None = None,
        upc: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        retailer_key = normalize_retailer_key(retailer)
        provider = provider_registry.get(retailer_key)
        if provider is None:
            available = ", ".join(provider_registry.list_keys()) or "none"
            await interaction.followup.send(
                f"Provider `{retailer_key}` is not registered. Available providers: `{available}`.",
                ephemeral=True,
            )
            return

        cleaned_sku = clean_optional(sku)
        cleaned_upc = clean_optional(upc)
        proof = await provider.check_local_inventory(
            LocalInventoryRequest(
                retailer=retailer_key,
                product_id=cleaned_sku or cleaned_upc,
                sku=cleaned_sku,
                upc=cleaned_upc,
                store_id=clean_optional(store_id),
                zip_code=clean_optional(zip_code),
                observed_price=observed_price,
                metadata={"requested_by": str(interaction.user.id)},
            )
        )

        await interaction.followup.send(embed=build_local_inventory_embed(proof), ephemeral=True)


def normalize_retailer_key(value: str) -> str:
    key = value.strip().lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    return RETAILER_ALIASES.get(key, key.replace(" ", "_"))


def clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def build_local_inventory_embed(proof: LocalInventoryProof) -> discord.Embed:
    title = f"{proof.retailer} Local Inventory Proof"
    embed = discord.Embed(
        title=title,
        description="Private proof preview only. SniperPlug will not public-alert weak local inventory candidates without stronger proof.",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Product", value=f"SKU: `{proof.sku or 'n/a'}`\nUPC: `{proof.upc or 'n/a'}`", inline=True)
    embed.add_field(name="Location", value=f"Store: `{proof.store_id or 'n/a'}`\nZIP: `{proof.zip_code or 'n/a'}`", inline=True)
    embed.add_field(
        name="Proof level",
        value=(
            f"`{proof.proof_level.value}`\n"
            f"Staff review: **{'Yes' if proof.should_staff_review else 'No'}**\n"
            f"Public alert: **{'Yes' if proof.should_public_alert else 'No'}**"
        ),
        inline=False,
    )

    if proof.local_price is not None or proof.online_price is not None:
        embed.add_field(
            name="Price",
            value=f"Local: **{money(proof.local_price)}**\nOnline: **{money(proof.online_price)}**",
            inline=True,
        )
    if proof.quantity_available is not None or proof.availability_text:
        embed.add_field(
            name="Inventory",
            value=f"Qty: `{proof.quantity_available if proof.quantity_available is not None else 'unknown'}`\n{proof.availability_text or 'No availability text.'}",
            inline=False,
        )
    if proof.clearance_signal:
        embed.add_field(
            name="Clearance signal",
            value=(
                f"Stage: **{proof.clearance_signal.stage.value}**\n"
                f"Ending: `.{proof.clearance_signal.price_ending or '??'}`\n"
                f"Confidence: `{proof.clearance_signal.confidence}/100`\n"
                f"{proof.clearance_signal.reason}"
            ),
            inline=False,
        )
    if proof.warnings:
        embed.add_field(name="Warnings", value="\n".join(f"• {warning}" for warning in proof.warnings[:5]), inline=False)

    embed.set_footer(text=f"Source: {proof.source} • Checked: {proof.checked_at}")
    return embed


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"
