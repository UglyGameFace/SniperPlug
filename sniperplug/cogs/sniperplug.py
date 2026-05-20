from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.alert_renderer import DealActionView, build_deal_embed
from sniperplug.services.risk_flags import apply_risk_flags
from sniperplug.services.routing import (
    ALERT_ROUTES,
    DEFAULT_ROUTE,
    ROUTE_DESCRIPTIONS,
    choose_primary_route,
    is_valid_route,
    route_label,
)


REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}

ROUTE_CHOICES = [
    app_commands.Choice(name=route_label(route), value=route)
    for route in ALERT_ROUTES
]


def missing_channel_permissions(channel: discord.TextChannel, member: discord.Member) -> list[str]:
    perms = channel.permissions_for(member)
    missing: list[str] = []

    for attr, label in REQUIRED_CHANNEL_PERMS.items():
        if not getattr(perms, attr, False):
            missing.append(label)

    return missing


class SniperPlugCog(commands.GroupCog, name="sniperplug"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Set the default channel where SniperPlug should post deal alerts.")
    @app_commands.describe(channel="The fallback channel for SniperPlug deal alerts.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        missing = self._missing_bot_perms(interaction.guild, channel)
        if missing:
            await interaction.response.send_message(
                self._missing_permissions_message(channel, missing),
                ephemeral=True,
            )
            return

        await self.bot.db.set_guild_deal_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"SniperPlug default deal alerts will post in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="set_channel", description="Route a SniperPlug alert type to a specific channel.")
    @app_commands.describe(
        route="The SniperPlug alert route to configure.",
        channel="The channel where this route should post.",
    )
    @app_commands.choices(route=ROUTE_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(
        self,
        interaction: discord.Interaction,
        route: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not is_valid_route(route.value):
            await interaction.response.send_message("That is not a valid SniperPlug alert route.", ephemeral=True)
            return

        missing = self._missing_bot_perms(interaction.guild, channel)
        if missing:
            await interaction.response.send_message(
                self._missing_permissions_message(channel, missing),
                ephemeral=True,
            )
            return

        await self.bot.db.set_alert_route(interaction.guild_id, route.value, channel.id)
        await interaction.response.send_message(
            f"{route_label(route.value)} alerts will post in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="routes", description="Show SniperPlug alert channel routing for this server.")
    async def routes(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        stats = await self.bot.db.stats(interaction.guild_id)
        routes = stats.get("alert_routes", {})

        embed = discord.Embed(title="SniperPlug Routes", color=discord.Color.orange())
        for route in ALERT_ROUTES:
            channel_id = routes.get(route)
            channel_text = f"<#{channel_id}>" if channel_id else "Not set"
            embed.add_field(
                name=route_label(route),
                value=f"{channel_text}\n{ROUTE_DESCRIPTIONS[route]}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="Show the current SniperPlug setup for this server.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        stats = await self.bot.db.stats(interaction.guild_id)
        channel_id = stats.get("deals_channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else "Not set"
        routes = stats.get("alert_routes", {})

        configured_routes = []
        for route in ALERT_ROUTES:
            route_channel_id = routes.get(route)
            if route_channel_id:
                configured_routes.append(f"**{route_label(route)}:** <#{route_channel_id}>")

        embed = discord.Embed(
            title="SniperPlug Status",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Default deals channel", value=channel_text, inline=False)
        embed.add_field(
            name="Configured routes",
            value="\n".join(configured_routes) if configured_routes else "No route-specific channels set yet.",
            inline=False,
        )
        embed.add_field(name="Deals stored", value=str(stats["deals_count"]), inline=True)
        embed.add_field(name="Dead reports", value=str(stats["dead_reports_count"]), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="test_alert", description="Post a realistic SniperPlug test deal alert.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_alert(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

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
            verification_status="demo",
            is_price_verified=False,
            is_link_verified=False,
            is_image_verified=False,
            verification_notes=[
                "Test alert uses demo data only.",
                "Future provider alerts must set real verification flags.",
            ],
        )
        deal = apply_risk_flags(deal)
        route_decision = choose_primary_route(deal)

        channel_id = await self.bot.db.resolve_alert_channel(interaction.guild_id, route_decision.route)
        channel = await self._resolve_text_channel(interaction.guild, channel_id, interaction.channel)

        if channel is None:
            await interaction.followup.send("I could not find a valid text channel to post the alert.", ephemeral=True)
            return

        missing = self._missing_bot_perms(interaction.guild, channel)
        if missing:
            await interaction.followup.send(
                self._missing_permissions_message(channel, missing),
                ephemeral=True,
            )
            return

        await self.bot.db.upsert_deal(deal)

        embed = build_deal_embed(deal)
        embed.add_field(
            name="Route",
            value=f"{route_label(route_decision.route)} • {route_decision.reason}",
            inline=False,
        )
        view = DealActionView(self.bot.db, deal)

        try:
            await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.followup.send(
                self._missing_permissions_message(channel, list(REQUIRED_CHANNEL_PERMS.values())),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Posted a SniperPlug test alert in {channel.mention} using route **{route_label(route_decision.route)}**.",
            ephemeral=True,
        )

    async def _resolve_text_channel(
        self,
        guild: discord.Guild,
        channel_id: int | None,
        fallback: discord.abc.GuildChannel | discord.InteractionChannel | None,
    ) -> discord.TextChannel | None:
        channel = None
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.DiscordException:
                    channel = None

        if channel is None:
            channel = fallback

        return channel if isinstance(channel, discord.TextChannel) else None

    def _missing_bot_perms(self, guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        bot_member = guild.me
        if bot_member is None and self.bot.user:
            bot_member = guild.get_member(self.bot.user.id)

        if bot_member is None:
            return []

        return missing_channel_permissions(channel, bot_member)

    def _missing_permissions_message(self, channel: discord.TextChannel, missing: list[str]) -> str:
        return (
            f"SniperPlug cannot post in {channel.mention} yet.\n\n"
            "**Missing channel permissions:**\n"
            + "\n".join(f"• {perm}" for perm in missing)
            + "\n\nGive the SniperPlug bot/role those permissions, then try again."
        )

    @setup.error
    @set_channel.error
    @test_alert.error
    async def admin_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to use this SniperPlug admin command."
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            message = (
                "Discord blocked SniperPlug with **403 Missing Access**. "
                "Check the configured alert channel and give the bot View Channel, Send Messages, Embed Links, and Read Message History."
            )
        else:
            message = f"SniperPlug hit an error: `{error}`"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
