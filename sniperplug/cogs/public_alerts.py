from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.providers.registry import provider_registry
from sniperplug.services.public_alert_settings import (
    get_public_alert_settings,
    parse_enabled_stores,
    set_public_alert_settings,
)


class PublicAlertsCog(commands.GroupCog, name="public_alerts"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="configure", description="Enable or disable public deal posting by store/provider.")
    @app_commands.describe(
        enabled="Turn public posting on or off.",
        channel="Optional public channel to post approved deal alerts into.",
        stores="Optional stores/providers. Use all, walmart, home_depot, bestbuy, amazon, or comma-separated values.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def configure(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        channel: discord.TextChannel | None = None,
        stores: str | None = None,
    ) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if channel is not None:
            missing = missing_channel_permissions(channel, interaction.guild.me) if interaction.guild.me else []
            if missing:
                await interaction.response.send_message(
                    f"SniperPlug cannot post in {channel.mention}. Missing: {', '.join(missing)}",
                    ephemeral=True,
                )
                return
            await self.bot.db.set_guild_deal_channel(interaction.guild_id, channel.id)

        available = tuple(provider_registry.list_keys())
        enabled_sources = parse_enabled_stores(stores, available_sources=available)
        await set_public_alert_settings(
            self.bot.db,
            interaction.guild_id,
            enabled=enabled,
            enabled_sources=enabled_sources,
        )

        channel_text = channel.mention if channel else "existing configured deal channel"
        store_text = "all stores" if not enabled_sources else ", ".join(enabled_sources)
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"Public deal posting is now **{state}** for **{store_text}**. Destination: {channel_text}.",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show public deal posting settings for this server.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        settings = await get_public_alert_settings(self.bot.db, interaction.guild_id)
        stats = await self.bot.db.stats(interaction.guild_id)
        channel_id = stats.get("deals_channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else "Not set"
        available = ", ".join(provider_registry.list_keys()) or "none"

        embed = discord.Embed(title="SniperPlug Public Alert Settings", color=discord.Color.orange())
        embed.add_field(name="Public posting", value="Enabled" if settings.enabled else "Disabled", inline=True)
        embed.add_field(name="Stores", value=settings.store_text, inline=True)
        embed.add_field(name="Destination", value=channel_text, inline=False)
        embed.add_field(name="Registered providers", value=available, inline=False)
        embed.set_footer(text="Only candidates that SniperPlug says would alert should be posted publicly.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @configure.error
    @status.error
    async def public_alerts_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to configure public alert posting."
        else:
            message = f"Public alert settings hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def missing_channel_permissions(channel: discord.TextChannel, member: discord.Member) -> list[str]:
    perms = channel.permissions_for(member)
    required = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History",
    }
    return [label for attr, label in required.items() if not getattr(perms, attr, False)]
