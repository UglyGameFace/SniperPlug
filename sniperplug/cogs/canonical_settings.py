from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.active_deals import active_deal_counts
from sniperplug.cogs.settings_dashboard import (
    build_command_guide_embed,
    build_db_health_snapshot,
    build_doctor_checks,
    doctor_next_action,
    format_active_counts,
    format_cache_counts,
    format_doctor_checks,
    format_provider_health,
    format_recent_errors,
)
from sniperplug.providers.registry import provider_registry
from sniperplug.services.autoscan_live_guild_reconciliation import (
    scheduler_membership_for_guild,
)
from sniperplug.services.command_catalog import entries_for_audience
from sniperplug.services.command_surface import command_surface_issues
from sniperplug.services.deal_threshold_settings import (
    get_starting_deal_percent,
    set_starting_deal_percent,
)
from sniperplug.services.error_logging import fetch_recent_error_events
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_posting import format_retailers
from sniperplug.services.walmart_catalog_coverage import catalog_route_pool
from sniperplug.services.walmart_exact_queue_health import load_walmart_exact_queue_health
from sniperplug.services.walmart_global_catalog_autoscan import load_global_catalog_state


DASHBOARD_VIEW_CHOICES = [
    app_commands.Choice(name="Overview", value="overview"),
    app_commands.Choice(name="Doctor / post-deploy checks", value="doctor"),
    app_commands.Choice(name="Command guide", value="commands"),
]

AUDIENCE_CHOICES = [
    app_commands.Choice(name="Everyone", value="everyone"),
    app_commands.Choice(name="Staff", value="staff"),
    app_commands.Choice(name="Owner", value="owner"),
]


class CanonicalSettingsCog(commands.Cog):
    """One owner dashboard instead of four overlapping status commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="sniperplug_dashboard",
        description="Open SniperPlug overview, doctor, or command guide.",
    )
    @app_commands.describe(
        view="Choose the dashboard page.",
        audience="Optional command-guide audience filter.",
    )
    @app_commands.choices(view=DASHBOARD_VIEW_CHOICES, audience=AUDIENCE_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sniperplug_dashboard(
        self,
        interaction: discord.Interaction,
        view: app_commands.Choice[str] | None = None,
        audience: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this in a server so I can show its SniperPlug settings.",
                ephemeral=True,
            )
            return

        selected = view.value if view is not None else "overview"
        if selected == "doctor":
            embed = await build_doctor_dashboard(self.bot, int(interaction.guild_id))
        elif selected == "commands":
            audience_value = audience.value if audience is not None else None
            embed = build_command_guide_embed(
                entries_for_audience(audience_value),
                audience_value,
            )
            embed.title = "SniperPlug Canonical Command Guide"
            embed.description = (
                "Only current commands are listed. Retired aliases and obsolete per-server autoscan controls are intentionally excluded."
            )
        else:
            embed = await build_overview_dashboard(self.bot, int(interaction.guild_id))

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="deal_threshold",
        description="Set this server's minimum exact Walmart markdown.",
    )
    @app_commands.describe(
        percent="Recommended: 30-40 for normal deals; 50+ for stricter alerts.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deal_threshold(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 0, 95],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this in a server so I can save its delivery threshold.",
                ephemeral=True,
            )
            return
        saved = await set_starting_deal_percent(
            self.bot.db,
            int(interaction.guild_id),
            int(percent),
        )
        embed = discord.Embed(
            title="✅ Server deal threshold updated",
            description=(
                f"This server will receive exact-verified Walmart deals at **{saved}%+ markdown**.\n\n"
                "The global catalog scanner still checks every configured route; this setting filters delivery only."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Recommended range",
            value="Use **30-40%** for normal coverage. Use **50%+** only for stricter glitch/clearance alerts.",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @sniperplug_dashboard.error
    @deal_threshold.error
    async def canonical_settings_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to use owner settings."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug settings failed safely: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def build_overview_dashboard(
    bot: commands.Bot,
    guild_id: int,
) -> discord.Embed:
    db = bot.db
    public_config = await get_public_alert_config(db, guild_id)
    threshold = await get_starting_deal_percent(db, guild_id)
    provider_health = await provider_registry.healthchecks()
    active_counts = await active_deal_counts(db, guild_id)
    enrolled, enrollment_reason = await scheduler_membership_for_guild(
        db,
        bot,
        guild_id,
    )
    global_state = await load_global_catalog_state(db)
    queue_health = await load_walmart_exact_queue_health(db)
    route_count = len(catalog_route_pool())

    ready = (
        bool(public_config.get("enabled"))
        and "walmart" in set(public_config.get("retailers") or ())
        and bool(public_config.get("channel_id"))
        and enrolled
    )
    embed = discord.Embed(
        title="🎛️ SniperPlug Dashboard",
        description=(
            "One canonical owner view for public delivery, global Walmart coverage, exact verification, providers, and cache."
        ),
        color=discord.Color.green() if ready else discord.Color.orange(),
    )
    channel_id = public_config.get("channel_id")
    embed.add_field(
        name="This server",
        value=(
            f"Public delivery: **{'on' if public_config.get('enabled') else 'off'}**\n"
            f"Channel: {f'<#{channel_id}>' if channel_id else '**not set**'}\n"
            f"Retailers: {format_retailers(public_config.get('retailers') or ())}\n"
            f"Minimum exact markdown: **{threshold}%+**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Global autoscan",
        value=(
            f"Live server fanout: **{'enrolled' if enrolled else 'blocked'}**\n"
            f"{enrollment_reason}\n"
            f"{global_state.summary_line(total_routes=route_count)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Exact verification",
        value=queue_health.summary_line(),
        inline=False,
    )
    embed.add_field(
        name="Active deal cache",
        value=format_active_counts(active_counts),
        inline=False,
    )
    embed.add_field(
        name="Providers",
        value=format_provider_health(provider_health),
        inline=False,
    )
    embed.add_field(
        name="Dashboard pages",
        value=(
            "• `view:Overview` — this page\n"
            "• `view:Doctor` — post-deploy checks and recent errors\n"
            "• `view:Commands` — the current command guide"
        ),
        inline=False,
    )
    embed.set_footer(
        text="Normal automatic coverage requires no /discover command. Use /autoscan_health for detailed delivery diagnosis."
    )
    return embed


async def build_doctor_dashboard(
    bot: commands.Bot,
    guild_id: int,
) -> discord.Embed:
    db = bot.db
    provider_health = await provider_registry.healthchecks()
    health = await build_db_health_snapshot(db, guild_id)
    errors = await fetch_recent_error_events(db, limit=5)
    checks = await build_doctor_checks(bot, guild_id, provider_health, health)

    surface_issues = command_surface_issues(bot.tree.get_commands())
    checks.append(
        (
            "PASS" if not surface_issues else "FAIL",
            "Canonical slash-command surface",
            "clean" if not surface_issues else " | ".join(surface_issues),
        )
    )
    failed = [check for check in checks if check[0] == "FAIL"]
    warnings = [check for check in checks if check[0] == "WARN"]
    status = "FAIL" if failed else "WARN" if warnings else "PASS"
    color = (
        discord.Color.red()
        if failed
        else discord.Color.orange()
        if warnings
        else discord.Color.green()
    )

    embed = discord.Embed(
        title=f"🩺 SniperPlug Doctor • {status}",
        description=(
            "Post-deploy checks for database, providers, cache, global autoscan, command conflicts, and recent errors."
        ),
        color=color,
    )
    embed.add_field(
        name="Core checks",
        value=format_doctor_checks(checks),
        inline=False,
    )
    embed.add_field(
        name="Providers",
        value=format_provider_health(provider_health),
        inline=False,
    )
    embed.add_field(
        name="Database/cache",
        value=format_cache_counts(health.get("counts", {})),
        inline=False,
    )
    embed.add_field(
        name="Recent errors",
        value=format_recent_errors(errors),
        inline=False,
    )
    embed.add_field(
        name="Next action",
        value=doctor_next_action(failed, warnings),
        inline=False,
    )
    return embed
