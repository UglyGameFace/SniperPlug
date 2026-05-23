from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.local_inventory import clean_optional, normalize_retailer_key
from sniperplug.models.local_inventory import clearance_signal_from_price


class ClearanceBankCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="seed_clearance", description="Save a clearance/penny lead so SniperPlug can track it without store API access.")
    @app_commands.describe(
        retailer="Retailer key or shorthand, like home, home_depot, walmart.",
        title="Short product name or description.",
        sku="Optional store SKU / item ID / internet number.",
        upc="Optional UPC if known.",
        product_url="Optional product URL.",
        store_id="Optional store ID where this was seen.",
        zip_code="Optional ZIP code.",
        observed_price="Optional observed local price, like 5.03 or 0.01.",
        notes="Optional notes, aisle, source, tag info, or what to verify.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def seed_clearance(
        self,
        interaction: discord.Interaction,
        retailer: str,
        title: str,
        sku: str | None = None,
        upc: str | None = None,
        product_url: str | None = None,
        store_id: str | None = None,
        zip_code: str | None = None,
        observed_price: float | None = None,
        notes: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so the seed can be saved to that server's clearance bank.", ephemeral=True)
            return

        retailer_key = normalize_retailer_key(retailer)
        seed_id = await self.bot.db.add_clearance_seed(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            retailer=retailer_key,
            title=title.strip(),
            sku=clean_optional(sku),
            upc=clean_optional(upc),
            product_url=clean_optional(product_url),
            store_id=clean_optional(store_id),
            zip_code=clean_optional(zip_code),
            observed_price=observed_price,
            notes=clean_optional(notes),
        )

        embed = discord.Embed(
            title="🌱 Clearance seed saved",
            description="This is a manual lead bank entry. It does not claim live inventory or a confirmed penny deal.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Retailer", value=f"`{retailer_key}`", inline=True)
        embed.add_field(name="Seed ID", value=f"`{seed_id[:10]}`", inline=True)
        embed.add_field(name="Product", value=title[:200], inline=False)
        embed.add_field(name="IDs", value=f"SKU: `{clean_optional(sku) or 'n/a'}`\nUPC: `{clean_optional(upc) or 'n/a'}`", inline=True)
        embed.add_field(name="Location", value=f"Store: `{clean_optional(store_id) or 'n/a'}`\nZIP: `{clean_optional(zip_code) or 'n/a'}`", inline=True)
        if observed_price is not None:
            signal = clearance_signal_from_price(observed_price)
            value = f"Observed: **${observed_price:,.2f}**"
            if signal:
                value += f"\nSignal: **{signal.stage.value}** (`.{signal.price_ending}`)"
            embed.add_field(name="Price signal", value=value, inline=False)
        if notes:
            embed.add_field(name="Notes", value=notes[:500], inline=False)
        embed.set_footer(text="Next: use /clearance_bank to list saved leads, then verify manually or with future provider checks.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="clearance_bank", description="List saved clearance/penny leads for this server.")
    @app_commands.describe(
        retailer="Optional retailer filter, like home or walmart.",
        limit="How many seeds to show. Max 25.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clearance_bank(
        self,
        interaction: discord.Interaction,
        retailer: str | None = None,
        limit: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which clearance bank to show.", ephemeral=True)
            return

        retailer_key = normalize_retailer_key(retailer) if retailer else None
        seeds = await self.bot.db.list_clearance_seeds(interaction.guild_id, retailer=retailer_key, limit=limit)
        if not seeds:
            await interaction.followup.send("No clearance seeds saved yet. Add one with `/seed_clearance`.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🌱 Clearance Seed Bank",
            description="Manual leads only. These need API/in-store verification before public alerts.",
            color=discord.Color.orange(),
        )
        for seed in seeds[:25]:
            price = seed.get("observed_price")
            price_text = f" • ${price:,.2f}" if price is not None else ""
            location = " / ".join(x for x in [seed.get("store_id"), seed.get("zip_code")] if x) or "no location"
            ids = " / ".join(x for x in [seed.get("sku"), seed.get("upc")] if x) or "no SKU/UPC"
            notes = seed.get("notes") or ""
            value = f"Retailer: `{seed['retailer']}`{price_text}\nIDs: `{ids}`\nLocation: `{location}`"
            if notes:
                value += f"\nNotes: {notes[:160]}"
            embed.add_field(name=seed["title"][:80], value=value[:1024], inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
