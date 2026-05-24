from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.active_deals import active_deal_counts
from sniperplug.cogs.public_alerts import format_auto_scan_status, get_public_alert_config, list_retailer_auto_scan_settings
from sniperplug.providers.registry import provider_registry
from sniperplug.services.public_posting import format_retailers


class SettingsDashboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="sniperplug_dashboard", description="Show SniperPlug posting, auto-scan, provider, and cache status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sniperplug_dashboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I can show that server settings.", ephemeral=True)
            return

        public_config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        provider_health = await provider_registry.healthchecks()
        active_counts = await active_deal_counts(self.bot.db, interaction.guild_id)
        channel_id = public_config.get("channel_id")
        channel_text = str(channel_id) if channel_id else "not set"

        embed = discord.Embed(title="SniperPlug Dashboard", description="Settings that decide whether SniperPlug scans, caches, and posts deals.", color=discord.Color.blue())
        embed.add_field(name="Public posting", value=f"Enabled: {'yes' if public_config['enabled'] else 'no'}\nChannel ID: {channel_text}\nRetailers: {format_retailers(public_config['retailers'])}", inline=False)
        embed.add_field(name="Auto-scan retailers", value=format_auto_scan_status(auto_scan), inline=False)
        embed.add_field(name="Active cache", value=format_active_counts(active_counts), inline=False)
        embed.add_field(name="Provider health", value=format_provider_health(provider_health), inline=False)
        embed.add_field(name="Recommended owner checks", value="Run public_alerts_status, retailer_autoscan_status, active_deals, and sniperplug providers after each deploy.", inline=False)
        embed.set_footer(text="Manual commands can run even when auto-scan is off. Auto-scan only controls scheduled pulls.")
        await interaction.followup.send(embed=embed, ephemeral=True)


def format_active_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "No active deals cached yet."
    return "\n".join(f"{retailer}: {count}" for retailer, count in sorted(counts.items()))


def format_provider_health(healthchecks) -> str:
    if not healthchecks:
        return "No providers registered."
    rows = []
    for health in healthchecks:
        status = getattr(health.status, "value", str(health.status))
        icon = "ready" if health.ok else "staged" if status == "staged" else "blocked"
        rows.append(f"{icon}: {health.provider_key} - {status} - {trim(health.message, 120)}")
    return "\n".join(rows[:10])


def trim(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."
