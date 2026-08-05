from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.public_alerts import (
    DealCategoriesShortcutView,
    DealCategoryDashboardView,
    public_alert_channel_status,
)
from sniperplug.services.autoscan_live_guild_reconciliation import (
    scheduler_membership_for_guild,
)
from sniperplug.services.deal_category_preferences import get_category_preferences
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.hp_watcher_health import load_hp_watcher_health
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.setup_self_heal import repair_public_alert_setup
from sniperplug.services.target_locations import get_guild_target_location
from sniperplug.services.target_watcher_health import load_target_watcher_health
from sniperplug.services.walmart_catalog_coverage import catalog_route_pool
from sniperplug.services.walmart_delivery_health import load_walmart_delivery_health
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
    guild_id = int(guild_id)
    repair = await repair_public_alert_setup(db, bot, guild_id)
    config = (
        repair.config
        if repair.config is not None
        else await get_public_alert_config(db, guild_id)
    )
    threshold = await get_starting_deal_percent(db, guild_id)
    category_preferences = await get_category_preferences(db, guild_id)
    enrolled, enrollment_reason = await scheduler_membership_for_guild(
        db,
        bot,
        guild_id,
    )
    global_state = await load_global_catalog_state(db)
    queue_health = await load_walmart_exact_queue_health(db)
    hp_health = await load_hp_watcher_health(db)
    target_health = await load_target_watcher_health(db)
    target_location = await get_guild_target_location(db, guild_id)
    target_location_ready = bool(target_location and target_location.enabled)
    delivery_health = await load_walmart_delivery_health(
        db,
        guild_id=guild_id,
        threshold=int(threshold),
        category_preferences=category_preferences,
    )
    channel_status = public_alert_channel_status(
        bot,
        guild_id,
        config.get("channel_id"),
    )
    routes = catalog_route_pool()
    retailers = set(config.get("retailers") or ())
    selected_retailers = retailers.intersection({"walmart", "hp", "target"})

    public_route_ready = (
        bool(config.get("enabled"))
        and channel_status.startswith("✅")
        and not repair.human_action_required
    )
    walmart_delivery_ready = (
        public_route_ready
        and "walmart" in selected_retailers
        and enrolled
    )
    hp_delivery_ready = (
        public_route_ready
        and "hp" in selected_retailers
        and hp_health.ok
    )
    target_delivery_ready = (
        public_route_ready
        and "target" in selected_retailers
        and target_location_ready
        and target_health.ok
    )

    readiness = {
        "walmart": walmart_delivery_ready,
        "hp": hp_delivery_ready,
        "target": target_delivery_ready,
    }
    selected_ready = [
        readiness[retailer]
        for retailer in selected_retailers
        if retailer in readiness
    ]
    any_selected_ready = bool(selected_ready and any(selected_ready))
    all_selected_ready = bool(selected_ready and all(selected_ready))

    if all_selected_ready and not delivery_health.has_delivery_problem:
        color = discord.Color.green()
    elif any_selected_ready:
        color = discord.Color.orange()
    else:
        color = discord.Color.red()

    embed = discord.Embed(
        title="🩺 Global Autoscan Health",
        description=(
            "Walmart discovery and exact verification run inside SniperPlug. "
            "HP Store and Target have independent watcher health. One unhealthy "
            "retailer does not silently disable another retailer."
        ),
        color=color,
    )
    embed.add_field(
        name="This server's delivery routes",
        value=(
            f"Public channel route: **{'ready' if public_route_ready else 'not ready'}**\n"
            f"Walmart delivery: **{_retailer_status('walmart', selected_retailers, walmart_delivery_ready)}**\n"
            f"HP Store delivery: **{_retailer_status('hp', selected_retailers, hp_delivery_ready)}**\n"
            f"Target delivery: **{_retailer_status('target', selected_retailers, target_delivery_ready)}**\n"
            f"Target location: **{'saved' if target_location_ready else 'not configured'}**\n"
            f"Walmart public threshold: **{threshold}%+**\n"
            "Global Walmart discovery starts at **10%** only to collect candidates; "
            "that is not permission to post below this server's threshold."
        ),
        inline=False,
    )
    if target_location_ready and target_location is not None:
        embed.add_field(
            name="This server's Target store",
            value=(
                f"**{target_location.store_name}**\n"
                f"{target_location.address_line}, {target_location.city}, "
                f"{target_location.state} {target_location.postal_code}\n"
                f"Store `{target_location.store_id}` • alert ZIP `{target_location.zip_code}`"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="This server's Target store",
            value=(
                "Not configured. Target remains off until `/target_location` "
                "saves an exact local store."
            ),
            inline=False,
        )
    embed.add_field(name="Channel", value=channel_status, inline=False)
    embed.add_field(name="Setup repair", value=repair.discord_line(), inline=False)
    embed.add_field(
        name="Walmart live fanout enrollment",
        value=(
            f"Enrolled: **{'yes' if enrolled else 'no'}**\n"
            f"{enrollment_reason}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Walmart delivery audit — current rules",
        value=trim_field(
            delivery_health.summary_line(threshold=int(threshold)),
            1024,
        ),
        inline=False,
    )
    embed.add_field(
        name="Global catalog coverage",
        value=(
            f"Walmart configured routes: **{len(routes)}**\n"
            f"{global_state.summary_line(total_routes=len(routes))}\n"
            "Target keeps one global TCIN catalog and advances bounded per-location "
            "cursors instead of copying the catalog for every server."
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

    target_health_detail = target_health.summary_line()
    if target_health.last_successful_cycle_at:
        target_health_detail += (
            f"\nLast successful cycle: `{target_health.last_successful_cycle_at}`"
        )
    if target_health.last_error:
        target_health_detail += (
            f"\nLast error: `{trim_field(target_health.last_error, 350)}`"
        )
    embed.add_field(
        name="Target multi-location watcher",
        value=trim_field(target_health_detail, 1024),
        inline=False,
    )

    embed.add_field(
        name="What the result means",
        value=(
            "A running catalog or exact queue does not guarantee a public post. "
            "A Walmart item posts only after exact item/offer/seller/variant proof, "
            f"the server's **{threshold}%+** threshold, category rules, and duplicate "
            "guards all pass. The audit above now shows which stage prevented delivery."
        ),
        inline=False,
    )

    next_action = _next_action(
        selected_retailers=selected_retailers,
        public_route_ready=public_route_ready,
        walmart_delivery_ready=walmart_delivery_ready,
        hp_delivery_ready=hp_delivery_ready,
        target_delivery_ready=target_delivery_ready,
        target_location_ready=target_location_ready,
        delivery_health=delivery_health,
        threshold=int(threshold),
    )
    if next_action:
        embed.add_field(name="Next action", value=next_action, inline=False)
    return embed


def _retailer_status(
    retailer: str,
    selected_retailers: set[str],
    ready: bool,
) -> str:
    if retailer not in selected_retailers:
        return "off"
    return "ready" if ready else "not ready"


def _next_action(
    *,
    selected_retailers: set[str],
    public_route_ready: bool,
    walmart_delivery_ready: bool,
    hp_delivery_ready: bool,
    target_delivery_ready: bool,
    target_location_ready: bool,
    delivery_health,
    threshold: int,
) -> str | None:
    if not public_route_ready or not selected_retailers:
        return (
            "Run `/setup_sniperplug_here` in the exact channel that should receive "
            "deals, then reopen this panel."
        )
    if "walmart" in selected_retailers and not walmart_delivery_ready:
        return (
            "Walmart is selected but is not enrolled in the live fanout set. "
            "Run `/setup_sniperplug_here` once in the saved public channel."
        )
    if delivery_health.eligible_without_post:
        return (
            "The audit found an exact event that currently passes the server rules "
            "without a durable public-post receipt. That is a real delivery-path "
            "warning, not a threshold miss."
        )
    if delivery_health.events_with_errors:
        return (
            "The global fanout table contains a recent error. The event remains "
            "visible in the audit instead of being reported as healthy."
        )
    if (
        "target" in selected_retailers
        and (not target_location_ready or not target_delivery_ready)
    ):
        return (
            "Target needs `/target_location` plus a healthy Target watcher. "
            "This does not block Walmart delivery."
        )
    if "hp" in selected_retailers and not hp_delivery_ready:
        return (
            "HP Store is enabled but its separate watcher is not healthy. "
            "This does not block Walmart; repair the HP deployment or turn HP off."
        )
    if delivery_health.events_seen and not delivery_health.posted:
        return (
            "No Walmart repair is indicated by the current audit. Recent exact "
            f"events were filtered by the **{threshold}%+** threshold, category, "
            "proof, or duplicate guards shown above."
        )
    if delivery_health.events_seen == 0:
        return (
            "No new exact Walmart event reached fanout in the audit window. "
            "The catalog can still be running normally; there was simply nothing "
            f"new that reached the server's **{threshold}%+** public gate."
        )
    return None


def trim_field(value: str, limit: int) -> str:
    text = str(value or "No detailed server decision has been recorded yet.")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
