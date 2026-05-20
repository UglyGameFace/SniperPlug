from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.alert_renderer import DealActionView, build_deal_embed
from sniperplug.services.risk_flags import apply_risk_flags


class SniperPlugCog(commands.GroupCog, name="sniperplug"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Set the channel where SniperPlug should post deal alerts.")
    @app_commands.describe(channel="The channel for SniperPlug deal alerts.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await self.bot.db.set_guild_deal_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"SniperPlug deal alerts will post in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show the current SniperPlug setup for this server.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        stats = await self.bot.db.stats(interaction.guild_id)
        channel_id = stats.get("deals_channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else "Not set"

        embed = discord.Embed(
            title="SniperPlug Status",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Deals channel", value=channel_text, inline=False)
        embed.add_field(name="Deals stored", value=str(stats["deals_count"]), inline=True)
        embed.add_field(name="Dead reports", value=str(stats["dead_reports_count"]), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="test_alert", description="Post a realistic SniperPlug test deal alert.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_alert(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        channel_id = await self.bot.db.get_guild_deal_channel(interaction.guild_id)
        channel = None

        if channel_id:
            channel = interaction.guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await interaction.guild.fetch_channel(channel_id)
                except discord.DiscordException:
                    channel = None

        if channel is None:
            channel = interaction.channel

        if not isinstance(channel, discord.abc.Messageable):
            await interaction.followup.send("I could not find a valid channel to post the alert.", ephemeral=True)
            return

        deal = NormalizedDeal(
            title="Apple iPhone 16 Pro Max, US Version, 256GB, Black Titanium - Unlocked (Renewed)",
            retailer="Amazon",
            product_url="https://www.amazon.com/",
            image_url="https://m.media-amazon.com/images/I/61giwQtR1qL._AC_SL1500_.jpg",
            current_price=179.99,
            typical_price=814.99,
            source="manual_test",
            marketplace="US",
            asin="B0DHJ896RY",
            seller_name="Example Merchant",
            fulfilled_by_amazon=False,
            fulfillment_type="Merchant Fulfilled",
            condition="Renewed",
            availability_message="Offer may not appear for every account.",
        )
        deal = apply_risk_flags(deal)

        await self.bot.db.upsert_deal(deal)

        embed = build_deal_embed(deal)
        view = DealActionView(self.bot.db, deal)

        await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"Posted a SniperPlug test alert in {channel.mention}.", ephemeral=True)

    @setup.error
    @test_alert.error
    async def admin_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to use this SniperPlug admin command."
        else:
            message = f"SniperPlug hit an error: `{error}`"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
