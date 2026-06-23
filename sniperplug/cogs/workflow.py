from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.public_alerts import (
    DEFAULT_AUTOSCAN_DAILY_LIMIT,
    DEFAULT_AUTOSCAN_INTERVAL_HOURS,
    UNMETERED_OFFICIAL_RETAILERS,
    format_daily_limit,
    format_interval,
    get_public_alert_config,
    list_retailer_auto_scan_settings,
    set_public_alert_config,
    set_retailer_auto_scan,
)
from sniperplug.services.public_posting import SUPPORTED_RETAILERS, format_retailers, normalize_retailer_key, parse_retailer_list


REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}


class WorkflowCog(commands.Cog):
    """Owner-friendly commands that hide the confusing setup split."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup_sniperplug", description="Recommended one-step setup for public alerts, retailers, and Walmart auto-scan.")
    @app_commands.describe(
        channel="Channel where verified public deal alerts should post.",
        retailers="Stores allowed to public-post. Default: walmart. Example: walmart,home_depot.",
        public_alerts="Allow verified deals to post publicly into the channel.",
        walmart_autoscan="Allow scheduled/background Walmart discovery. Manual scans always work.",
        walmart_unlimited="For Walmart only: remove scheduled interval/daily gates because official API is unmetered here.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_sniperplug(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        retailers: str = "walmart",
        public_alerts: bool = True,
        walmart_autoscan: bool = True,
        walmart_unlimited: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._apply_setup(
            interaction,
            channel=channel,
            retailers=retailers,
            public_alerts=public_alerts,
            walmart_autoscan=walmart_autoscan,
            walmart_unlimited=walmart_unlimited,
        )

    @app_commands.command(name="setup_sniperplug_here", description="Setup SniperPlug to post and auto-scan from this channel.")
    @app_commands.describe(
        retailers="Stores allowed to public-post. Default: walmart.",
        public_alerts="Allow verified deals to post publicly into this channel.",
        walmart_autoscan="Allow scheduled/background Walmart discovery. Manual scans always work.",
        walmart_unlimited="For Walmart only: remove scheduled interval/daily gates because official API is unmetered here.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_sniperplug_here(
        self,
        interaction: discord.Interaction,
        retailers: str = "walmart",
        public_alerts: bool = True,
        walmart_autoscan: bool = True,
        walmart_unlimited: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("Run this inside the text channel where SniperPlug should post verified deals, or use `/setup_sniperplug` and pick a channel.", ephemeral=True)
            return
        await self._apply_setup(
            interaction,
            channel=channel,
            retailers=retailers,
            public_alerts=public_alerts,
            walmart_autoscan=walmart_autoscan,
            walmart_unlimited=walmart_unlimited,
        )

    async def _apply_setup(
        self,
        interaction: discord.Interaction,
        *,
        channel: discord.TextChannel,
        retailers: str,
        public_alerts: bool,
        walmart_autoscan: bool,
        walmart_unlimited: bool,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.followup.send("Use this in a server so SniperPlug can save that server's workflow settings.", ephemeral=True)
            return
        if channel.guild.id != interaction.guild_id:
            await interaction.followup.send("That channel is not in this server. Run setup inside the target server/channel.", ephemeral=True)
            return

        missing = missing_channel_permissions(channel, interaction.guild.me)
        if missing:
            await interaction.followup.send(missing_permissions_message(channel, missing), ephemeral=True)
            return

        parsed_retailers = parse_retailer_list(retailers) or ("walmart",)
        unsupported = [retailer for retailer in parsed_retailers if normalize_retailer_key(retailer) not in SUPPORTED_RETAILERS]
        if unsupported:
            await interaction.followup.send(
                f"Unsupported retailer(s): `{', '.join(unsupported)}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}",
                ephemeral=True,
            )
            return

        await self.bot.db.set_guild_deal_channel(interaction.guild_id, channel.id)
        await set_public_alert_config(
            self.bot.db,
            guild_id=interaction.guild_id,
            enabled=public_alerts,
            retailers=tuple(normalize_retailer_key(retailer) for retailer in parsed_retailers),
            channel_id=channel.id,
        )

        if walmart_unlimited and "walmart" not in UNMETERED_OFFICIAL_RETAILERS:
            walmart_unlimited = False
        if walmart_unlimited:
            await set_retailer_auto_scan(self.bot.db, interaction.guild_id, "walmart", walmart_autoscan, interval_hours=0, daily_limit=0)
        else:
            await set_retailer_auto_scan(
                self.bot.db,
                interaction.guild_id,
                "walmart",
                walmart_autoscan,
                interval_hours=DEFAULT_AUTOSCAN_INTERVAL_HOURS,
                daily_limit=DEFAULT_AUTOSCAN_DAILY_LIMIT,
            )

        public_config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        auto_scan_settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        await interaction.followup.send(embed=build_setup_complete_embed(channel, public_config, auto_scan_settings), ephemeral=True)

    @app_commands.command(name="sniperplug_workflow", description="Show the simple SniperPlug workflow from setup to posting.")
    async def sniperplug_workflow(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="SniperPlug Workflow",
            description="Use this order so the bot feels simple instead of scattered.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="1. Setup once", value="Fastest: run `/setup_sniperplug_here` inside the deal channel. Advanced: run `/setup_sniperplug` and choose a channel. Both default to public Walmart posting plus Walmart background auto-scan.", inline=False)
        embed.add_field(name="2. Manual testing", value="Use `/deals` for one item, `/hunt` for category buttons, or `/discover` for broad manual discovery. Manual scans do not depend on auto-scan being enabled.", inline=False)
        embed.add_field(name="3. Background scanning", value="Use `/retailer_autoscan` when you want to change scheduled/background pulls. Paid-credit providers stay protected; Walmart can run unlimited through official-provider bypass.", inline=False)
        embed.add_field(name="4. Troubleshooting", value="Use `/autoscan_health`, `/sniperplug_dashboard`, `/active_deals`, and `/sniperplug_commands` to see what is configured, cached, and available.", inline=False)
        embed.set_footer(text="Public posting requires public alerts ON, an alert channel, allowed retailers, and alertable proof.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_setup_complete_embed(channel: discord.TextChannel, public_config: dict, auto_scan_settings: dict[str, dict]) -> discord.Embed:
    walmart = auto_scan_settings.get("walmart", {})
    interval = int(walmart.get("interval_hours") if walmart.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
    daily = int(walmart.get("daily_limit") if walmart.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
    embed = discord.Embed(
        title="SniperPlug setup complete",
        description="This sets the default route, public posting rules, and Walmart background scan together so deals do not silently cache without posting.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Public alert channel", value=channel.mention, inline=True)
    embed.add_field(name="Public posting", value="ON" if public_config.get("enabled") else "OFF", inline=True)
    embed.add_field(name="Public retailers", value=format_retailers(public_config.get("retailers") or ()), inline=False)
    embed.add_field(
        name="Walmart background auto-scan",
        value=(
            f"Enabled: **{'yes' if walmart.get('enabled') else 'no'}**\n"
            f"Interval: **{format_interval(interval)}**\n"
            f"Daily limit: **{format_daily_limit(daily)}**\n"
            "Manual `/deals`, `/hunt`, and `/discover` still work even when background auto-scan is off."
        ),
        inline=False,
    )
    embed.add_field(name="Next test", value="Run `/autoscan_now force:true`, `/deals search:turtle wax`, or `/discover`. If cards are cached but not posted, check `/autoscan_health`, `/active_deals`, and `/sniperplug_dashboard`.", inline=False)
    return embed


def missing_channel_permissions(channel: discord.TextChannel, member: discord.Member | None) -> list[str]:
    if member is None:
        return []
    perms = channel.permissions_for(member)
    return [label for attr, label in REQUIRED_CHANNEL_PERMS.items() if not getattr(perms, attr, False)]


def missing_permissions_message(channel: discord.TextChannel, missing: list[str]) -> str:
    return (
        f"SniperPlug cannot post in {channel.mention} yet.\n\n"
        "Missing channel permissions:\n"
        + "\n".join(f"• {perm}" for perm in missing)
        + "\n\nGive the SniperPlug bot/role those permissions, then run `/setup_sniperplug_here` in that channel."
    )
