from __future__ import annotations

from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.public_alerts import set_retailer_auto_scan
from sniperplug.services.deal_category_preferences import apply_preset
from sniperplug.services.deal_threshold_settings import (
    get_starting_deal_percent,
    set_starting_deal_percent,
)
from sniperplug.services.public_alert_config import (
    get_public_alert_config,
    set_public_alert_config,
)
from sniperplug.services.public_posting import normalize_retailer_key


REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}


class CanonicalWorkflowCog(commands.Cog):
    """One setup command for global discovery and per-server delivery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup_sniperplug_here",
        description="Use this channel for exact-verified deal alerts.",
    )
    @app_commands.describe(
        public_alerts="Allow exact-verified Walmart and HP Store deals in this channel.",
        threshold="Minimum exact markdown for this server. Recommended: 30-40.",
        best_categories="Apply broad Deal Week and Walmart Cash category coverage.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_sniperplug_here(
        self,
        interaction: discord.Interaction,
        public_alerts: bool = True,
        threshold: app_commands.Range[int, 0, 95] | None = None,
        best_categories: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.followup.send(
                "Use this in the server and text channel where SniperPlug should post.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "Run this inside the text channel where SniperPlug should post verified deals.",
                ephemeral=True,
            )
            return

        missing = missing_channel_permissions(channel, interaction.guild.me)
        if missing:
            await interaction.followup.send(
                missing_permissions_message(channel, missing),
                ephemeral=True,
            )
            return

        guild_id = int(interaction.guild_id)
        existing_config = await get_public_alert_config(self.bot.db, guild_id)
        retailers = merge_canonical_retailers(existing_config.get("retailers") or ())
        await self.bot.db.set_guild_deal_channel(guild_id, int(channel.id))
        await set_public_alert_config(
            self.bot.db,
            guild_id=guild_id,
            enabled=bool(public_alerts),
            retailers=retailers,
            channel_id=int(channel.id),
        )

        # Walmart discovery runs globally inside SniperPlug. HP discovery runs in
        # its own process and writes exact events to the same shared database.
        # Neither path creates one duplicate scanner per Discord server.
        await set_retailer_auto_scan(
            self.bot.db,
            guild_id,
            "walmart",
            bool(public_alerts),
            interval_hours=0,
            daily_limit=0,
        )

        if threshold is not None:
            await set_starting_deal_percent(self.bot.db, guild_id, int(threshold))
        saved_threshold = await get_starting_deal_percent(self.bot.db, guild_id)

        if best_categories:
            await apply_preset(self.bot.db, guild_id, "deal_week")
            await apply_preset(self.bot.db, guild_id, "walmart_cash")

        config = await get_public_alert_config(self.bot.db, guild_id)
        embed = discord.Embed(
            title="✅ SniperPlug delivery setup complete",
            description=(
                "SniperPlug now receives exact-verified deals from its global Walmart scanner and the standalone HP Store watcher. "
                "This server only receives delivery fanout; it never launches duplicate retailer scans."
            ),
            color=discord.Color.green() if config.get("enabled") else discord.Color.orange(),
        )
        embed.add_field(name="Alert channel", value=channel.mention, inline=True)
        embed.add_field(
            name="Public delivery",
            value="Enabled" if config.get("enabled") else "Paused",
            inline=True,
        )
        embed.add_field(
            name="Server filter",
            value=f"Exact retailer markdown: **{saved_threshold}%+**",
            inline=True,
        )
        embed.add_field(
            name="Retailers",
            value="**Walmart** global catalog + **HP Store** standalone exact-price watcher",
            inline=False,
        )
        embed.add_field(
            name="How autoscan works now",
            value=(
                "• One durable global cursor covers Walmart routes.\n"
                "• The separate HP watcher covers HP's US product catalog and writes verified events into SniperPlug's shared database.\n"
                "• Exact deals fan out using this server's threshold, categories, channel, and duplicate rules.\n"
                "• `/discover` remains an optional manual sweep—not a requirement for automatic coverage."
            ),
            inline=False,
        )
        embed.add_field(
            name="Next check",
            value="Run `/autoscan_health` to confirm this server is enrolled in live fanout.",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @setup_sniperplug_here.error
    async def setup_sniperplug_here_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to configure SniperPlug."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug setup failed safely: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def merge_canonical_retailers(existing: Iterable[str]) -> tuple[str, ...]:
    """Add canonical sources without deleting a server's other store choices."""

    merged: list[str] = []
    for value in (*tuple(existing), "walmart", "hp"):
        retailer = normalize_retailer_key(value)
        if retailer and retailer not in merged:
            merged.append(retailer)
    return tuple(merged)


def missing_channel_permissions(
    channel: discord.TextChannel,
    member: discord.Member | None,
) -> list[str]:
    if member is None:
        return []
    permissions = channel.permissions_for(member)
    return [
        label
        for attribute, label in REQUIRED_CHANNEL_PERMS.items()
        if not getattr(permissions, attribute, False)
    ]


def missing_permissions_message(
    channel: discord.TextChannel,
    missing: list[str],
) -> str:
    return (
        f"SniperPlug cannot post in {channel.mention} yet.\n\n"
        "Missing channel permissions:\n"
        + "\n".join(f"• {permission}" for permission in missing)
        + "\n\nFix those permissions, then run `/setup_sniperplug_here` again in this channel."
    )
