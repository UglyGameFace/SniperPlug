from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.public_alerts import (
    DealCategoriesShortcutView,
    DealCategoryDashboardView,
    public_alert_channel_status,
)
from sniperplug.services.autoscan_history import (
    format_latest_report_line,
    latest_autoscan_report,
)
from sniperplug.services.autoscan_live_guild_reconciliation import (
    scheduler_membership_for_guild,
)
from sniperplug.services.deal_category_preferences import get_category_preferences
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.hp_watcher_health import load_hp_watcher_health
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.setup_self_heal import repair_public_alert_setup
from sniperplug.services.walmart_catalog_coverage import catalog_route_pool
from sniperplug.services.walmart_exact_queue_health import load_walmart_exact_queue_health
from sniperplug.services.walmart_global_catalog_autoscan import load_global_catalog_state


class CanonicalPublicAlertsCog(commands.Cog):
    """Canonical category controls and global autoscan delivery health."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="deal_categories",
        description="Choose which verified deal categories this server receives.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deal_categories(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this in a server so I know which category settings to show.",
                ephemeral=True,
            )
            return
        preferences = await get_category_preferences(
            self.bot.db,
            int(interaction.guild_id),
        )
        view = DealCategoryDashboardView(
            self.bot.db,
            int(interaction.guild_id),
            preferences,
        )
        await interaction.followup.send(
            embed=view.embed(),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="autoscan_health",
        description="Check global retailer coverage and this server's deal delivery.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this in a server so I can check that server's delivery enrollment.",
                ephemeral=True,
            )
            return

        guild_id = int(interaction.guild_id)
        embed = await build_global_autoscan_health_embed(
            self.bot,
            guild_id,
        )
        await interaction.followup.send(
            embed=embed,
            view=DealCategoriesShortcutView(self.bot.db, guild_id),
            ephemeral=True,
        )

    @deal_categories.error
    @autoscan_health.error
    async def canonical_public_alert_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to use these server controls."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug server controls failed safely: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def build_global_autoscan_health_embed(
    bot: commands.Bot,
    guild_id: int,
) -> discord.Embed:
    db = bot.db
    repair = await repair_public_alert_setup(db, bot, int(guild_id))
    config = (
        repair.config
        if repair.config is not None
        else await get_public_alert_config(db, int(guild_id))
    )
    threshold = await get_starting_deal_percent(db, int(guild_id))
    enrolled, enrollment_reason = await scheduler_membership_for_guild(
        db,
        bot,
        int(guild_id),
    )
    global_state = await load_global_catalog_state(db)
    queue_health = await load_walmart_exact_queue_health(db)
    hp_health = await load_hp_watcher_health(db)
    latest_report = await latest_autoscan_report(
        db,
        guild_id=int(guild_id),
        retailer="walmart",
        scan_key="autoscan:walmart_discovery",
    )
    channel_status = public_alert_channel_status(
        bot,
        int(guild_id),
        config.get("channel_id"),
    )
    routes = catalog_route_pool()
    retailers = set(config.get("retailers") or ())
    selected_retailers = retailers.intersection({"walmart", "hp"})

    base_delivery_ready = (
        bool(config.get("enabled"))
        and channel_status.startswith("✅")
        and enrolled
        and not repair.human_action_required
    )
    walmart_delivery_ready = base_delivery_ready and "walmart" in selected_retailers
    hp_delivery_ready = base_delivery_ready and "hp" in selected_retailers and hp_health.ok
    delivery_ready = (
        base_delivery_ready
        and bool(selected_retailers)
        and ("walmart" not in selected_retailers or walmart_delivery_ready)
        and ("hp" not in selected_retailers or hp_delivery_ready)
    )
    embed = discord.Embed(
        title="🩺 Global Autoscan Health",
        description=(
            "Walmart discovery runs once inside SniperPlug, while HP Store discovery runs in its own watcher process. "
            "Both feed exact-verified deals through this server's shared delivery controls."
        ),
        color=discord.Color.green() if delivery_ready else discord.Color.orange(),
    )
    embed.add_field(
        name="This server's delivery",
        value=(
            f"Ready: **{'yes' if delivery_ready else 'no'}**\n"
            f"Public alerts: **{'on' if config.get('enabled') else 'off'}**\n"
            f"Walmart enabled: **{'yes' if 'walmart' in retailers else 'no'}**\n"
            f"HP Store enabled: **{'yes' if 'hp' in retailers else 'no'}**\n"
            f"Minimum exact markdown: **{threshold}%+**"
        ),
        inline=False,
    )
    embed.add_field(name="Channel", value=channel_status, inline=False)
    embed.add_field(name="Setup repair", value=repair.discord_line(), inline=False)
    embed.add_field(
        name="Live fanout enrollment",
        value=(
            f"Enrolled: **{'yes' if enrolled else 'no'}**\n"
            f"{enrollment_reason}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Global catalog coverage",
        value=(
            f"Walmart configured routes: **{len(routes)}**\n"
            f"{global_state.summary_line(total_routes=len(routes))}\n"
            "A durable cursor advances in order and resumes after restarts. Per-server interval settings no longer control discovery."
        ),
        inline=False,
    )
    embed.add_field(
        name="Exact verification queue — Walmart",
        value=queue_health.summary_line(),
        inline=False,
    )
    hp_health_detail = hp_health.summary_line()
    if hp_health.last_successful_cycle_at:
        hp_health_detail += f"\nLast successful cycle: `{hp_health.last_successful_cycle_at}`"
    if hp_health.last_error:
        hp_health_detail += f"\nLast error: `{trim_field(hp_health.last_error, 350)}`"
    embed.add_field(
        name="HP Store standalone watcher",
        value=trim_field(hp_health_detail, 1024),
        inline=False,
    )
    embed.add_field(
        name="Latest Walmart server decision",
        value=trim_field(format_latest_report_line(latest_report), 1024),
        inline=False,
    )
    embed.add_field(
        name="What commands are actually needed",
        value=(
            "Normal automatic coverage needs no manual command. `/discover` is optional for an immediate Walmart sweep, "
            "and `/autoscan_now` is an owner diagnostic—not either background engine."
        ),
        inline=False,
    )
    if not base_delivery_ready or not selected_retailers:
        embed.add_field(
            name="Next action",
            value=(
                "Run `/setup_sniperplug_here` in the exact channel that should receive deals, then reopen this panel. "
                "The global scanners keep running even when one server's delivery setup needs repair."
            ),
            inline=False,
        )
    elif "hp" in selected_retailers and not hp_health.ok:
        embed.add_field(
            name="Next action",
            value=(
                "This server's delivery is configured, but the HP watcher is not reporting healthy. "
                "The bot owner should check the separate HP watcher deployment and confirm it uses the same Turso database as SniperPlug."
            ),
            inline=False,
        )
    return embed


def trim_field(value: str, limit: int) -> str:
    text = str(value or "No detailed server decision has been recorded yet.")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
