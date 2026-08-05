from __future__ import annotations

from typing import Any

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
from sniperplug.services.walmart_delivery_recovery import (
    WalmartRecoveryActionResult,
    WalmartRecoveryItem,
    load_walmart_recovery_items,
    post_walmart_owner_override,
    recheck_walmart_exact_offer,
    retry_walmart_delivery_current_rules,
    share_walmart_manual_lead,
)
from sniperplug.services.walmart_exact_queue_health import load_walmart_exact_queue_health
from sniperplug.services.walmart_global_catalog_autoscan import load_global_catalog_state


RECOVERY_VIEW_TIMEOUT_SECONDS = 600
RECOVERY_SELECT_LIMIT = 25


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

    @app_commands.command(
        name="walmart_recovery",
        description="Review and recover recent Walmart deals that did not post.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def walmart_recovery(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.followup.send(
                "Use this in a server so I can inspect that server's Walmart delivery decisions.",
                ephemeral=True,
            )
            return

        guild_id = int(interaction.guild_id)
        threshold = await get_starting_deal_percent(self.bot.db, guild_id)
        category_preferences = await get_category_preferences(self.bot.db, guild_id)
        items = await load_walmart_recovery_items(
            self.bot.db,
            guild_id=guild_id,
            threshold=int(threshold),
            category_preferences=category_preferences,
        )
        view = WalmartRecoveryView(
            bot=self.bot,
            guild_id=guild_id,
            requester_id=int(interaction.user.id),
            server_owner_id=int(interaction.guild.owner_id),
            threshold=int(threshold),
            category_preferences=category_preferences,
            items=items,
        )
        message = await interaction.followup.send(
            embed=view.embed(),
            view=view,
            ephemeral=True,
            wait=True,
        )
        view.message = message

    @deal_categories.error
    @autoscan_health.error
    @walmart_recovery.error
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


class WalmartRecoveryView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        guild_id: int,
        requester_id: int,
        server_owner_id: int,
        threshold: int,
        category_preferences: dict[str, str],
        items: list[WalmartRecoveryItem],
    ) -> None:
        super().__init__(timeout=RECOVERY_VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.guild_id = int(guild_id)
        self.requester_id = int(requester_id)
        self.server_owner_id = int(server_owner_id)
        self.threshold = int(threshold)
        self.category_preferences = dict(category_preferences or {})
        self.items = list(items[:RECOVERY_SELECT_LIMIT])
        self.selected_index = 0
        self.message: Any | None = None
        self.rebuild_components()

    @property
    def requester_is_server_owner(self) -> bool:
        return self.requester_id == self.server_owner_id

    @property
    def selected_item(self) -> WalmartRecoveryItem | None:
        if not self.items:
            return None
        self.selected_index = max(0, min(self.selected_index, len(self.items) - 1))
        return self.items[self.selected_index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.requester_id:
            return True
        await interaction.response.send_message(
            "Open your own `/walmart_recovery` panel to use these controls.",
            ephemeral=True,
        )
        return False

    async def reload(self) -> None:
        self.items = await load_walmart_recovery_items(
            self.bot.db,
            guild_id=self.guild_id,
            threshold=self.threshold,
            category_preferences=self.category_preferences,
        )
        self.items = self.items[:RECOVERY_SELECT_LIMIT]
        self.selected_index = max(0, min(self.selected_index, len(self.items) - 1))
        self.rebuild_components()

    async def refresh_message(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.embed(), view=self)
        except Exception:
            return

    def rebuild_components(self) -> None:
        self.clear_items()
        item = self.selected_item
        if not self.items or item is None:
            return

        options: list[discord.SelectOption] = []
        for index, recovery_item in enumerate(self.items):
            discount = (
                "?%"
                if recovery_item.discount is None
                else f"{recovery_item.discount:.0f}%"
            )
            options.append(
                discord.SelectOption(
                    label=trim_component_label(
                        f"{index + 1}. {recovery_item.label}",
                        100,
                    ),
                    value=str(index),
                    description=trim_component_label(
                        f"{discount} • {outcome_label(recovery_item.outcome)}",
                        100,
                    ),
                    default=index == self.selected_index,
                )
            )
        self.add_item(WalmartRecoverySelect(options=options))
        self.add_item(
            WalmartRecoveryActionButton(
                action="retry",
                label="Retry current rules",
                emoji="🔁",
                style=discord.ButtonStyle.primary,
                disabled=not item.can_retry_current_rules,
            )
        )
        self.add_item(
            WalmartRecoveryActionButton(
                action="override",
                label="Post once (owner)",
                emoji="📣",
                style=discord.ButtonStyle.danger,
                disabled=(
                    not self.requester_is_server_owner
                    or not item.can_owner_override
                ),
            )
        )
        self.add_item(
            WalmartRecoveryActionButton(
                action="recheck",
                label="Recheck exact offer",
                emoji="🧪",
                style=discord.ButtonStyle.secondary,
                disabled=not item.can_recheck_exact,
            )
        )
        self.add_item(
            WalmartRecoveryActionButton(
                action="lead",
                label="Share as lead (owner)",
                emoji="🟨",
                style=discord.ButtonStyle.secondary,
                disabled=(
                    not self.requester_is_server_owner
                    or not item.can_share_manual_lead
                ),
            )
        )
        url = str(getattr(item.card, "url", "") or "") if item.card else ""
        if url.startswith(("https://", "http://")):
            self.add_item(
                discord.ui.Button(
                    label="Open Walmart",
                    emoji="🔗",
                    style=discord.ButtonStyle.link,
                    url=url,
                )
            )

    def embed(self) -> discord.Embed:
        item = self.selected_item
        if item is None:
            return discord.Embed(
                title="🧰 Walmart Delivery Recovery",
                description=(
                    "No recent unposted exact Walmart events need recovery. "
                    "Automatic scanning can still be running normally when no item "
                    f"meets this server's **{self.threshold}%+** public rules."
                ),
                color=discord.Color.green(),
            )

        discount = (
            "Unknown"
            if item.discount is None
            else f"{item.discount:.1f}%"
        )
        embed = discord.Embed(
            title="🧰 Walmart Delivery Recovery",
            description=(
                "Inspect the exact reason a recent Walmart event did not post. "
                "Safe retry keeps every automatic rule. **Post once** can bypass only "
                "a soft server rule and is restricted to the actual server owner."
            ),
            color=recovery_color(item.outcome),
        )
        embed.add_field(
            name="Selected event",
            value=(
                f"**{trim_field(item.label, 300)}**\n"
                f"Outcome: **{outcome_label(item.outcome)}**\n"
                f"Reason: {trim_field(item.detail, 500)}\n"
                f"Discount: **{discount}** • server threshold: **{self.threshold}%+**\n"
                f"Walmart item: **{item.item_id or 'unavailable'}**\n"
                f"Event: `{trim_field(item.deal_key, 260)}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Available recovery paths",
            value=recovery_actions_line(
                item,
                is_server_owner=self.requester_is_server_owner,
            ),
            inline=False,
        )
        embed.add_field(
            name="Safety boundary",
            value=(
                "Threshold, category, and duplicate decisions are **soft** and can be "
                "overridden once by the server owner. Missing exact item/offer/seller/"
                "variant or structured-price proof is **hard**: recheck it, or share it "
                "only as a clearly labeled manual lead—not as a verified deal."
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Showing {self.selected_index + 1}/{len(self.items)} recent unposted events"
            )
        )
        return embed


class WalmartRecoverySelect(discord.ui.Select):
    def __init__(self, *, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose an unposted Walmart event",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            custom_id="walmart_recovery_select:v1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, WalmartRecoveryView):
            await interaction.response.send_message(
                "This recovery panel is no longer active.",
                ephemeral=True,
            )
            return
        try:
            view.selected_index = int(self.values[0])
        except (TypeError, ValueError, IndexError):
            await interaction.response.send_message(
                "That recovery event could not be selected.",
                ephemeral=True,
            )
            return
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.embed(), view=view)


class WalmartRecoveryActionButton(discord.ui.Button):
    def __init__(
        self,
        *,
        action: str,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        disabled: bool,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            disabled=disabled,
            row=1,
            custom_id=f"walmart_recovery_action:{action}:v1",
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, WalmartRecoveryView):
            await interaction.response.send_message(
                "This recovery panel is no longer active.",
                ephemeral=True,
            )
            return
        item = view.selected_item
        if item is None:
            await interaction.response.send_message(
                "There is no selected recovery item.",
                ephemeral=True,
            )
            return

        if self.action in {"override", "lead"}:
            if int(interaction.user.id) != view.server_owner_id:
                await interaction.response.send_message(
                    "Only the actual server owner can bypass a soft rule or publish an unverified manual lead.",
                    ephemeral=True,
                )
                return
            confirm = WalmartRecoveryConfirmView(
                parent=view,
                item=item,
                action=self.action,
            )
            warning = (
                "This posts the exact event **once** while bypassing only its soft "
                "threshold/category/duplicate reason. Exact proof must still pass."
                if self.action == "override"
                else "This posts a clearly labeled **manual review lead**, not a verified automatic deal."
            )
            await interaction.response.send_message(
                f"**Confirm recovery action**\n{warning}\n\n{item.compact_reason()}",
                view=confirm,
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        if self.action == "retry":
            result = await retry_walmart_delivery_current_rules(
                bot=view.bot,
                guild_id=view.guild_id,
                item=item,
                actor_id=int(interaction.user.id),
            )
        elif self.action == "recheck":
            result = await recheck_walmart_exact_offer(
                db=view.bot.db,
                guild_id=view.guild_id,
                item=item,
                actor_id=int(interaction.user.id),
            )
        else:
            result = WalmartRecoveryActionResult(False, "Unknown recovery action.")

        await view.reload()
        await interaction.edit_original_response(embed=view.embed(), view=view)
        await interaction.followup.send(
            recovery_result_text(result),
            ephemeral=True,
        )


class WalmartRecoveryConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        parent: WalmartRecoveryView,
        item: WalmartRecoveryItem,
        action: str,
    ) -> None:
        super().__init__(timeout=60)
        self.parent = parent
        self.item = item
        self.action = action

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.parent.server_owner_id:
            return True
        await interaction.response.send_message(
            "Only the actual server owner can confirm this action.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Confirm",
        emoji="✅",
        style=discord.ButtonStyle.danger,
        custom_id="walmart_recovery_confirm:v1",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.action == "override":
            result = await post_walmart_owner_override(
                bot=self.parent.bot,
                guild_id=self.parent.guild_id,
                item=self.item,
                actor_id=int(interaction.user.id),
            )
        elif self.action == "lead":
            result = await share_walmart_manual_lead(
                bot=self.parent.bot,
                guild_id=self.parent.guild_id,
                item=self.item,
                actor_id=int(interaction.user.id),
            )
        else:
            result = WalmartRecoveryActionResult(False, "Unknown recovery action.")

        await self.parent.reload()
        await self.parent.refresh_message()
        await interaction.followup.send(
            recovery_result_text(result),
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="walmart_recovery_cancel:v1",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Recovery action cancelled. No deal was posted or requeued.",
            view=None,
        )


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
            "HP Store and Target have independent watcher health. "
            "One unhealthy retailer does not silently disable another retailer."
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
        name="Live fanout enrollment",
        value=(
            f"Walmart enrolled: **{'yes' if enrolled else 'no'}**\n"
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
            "guards all pass. Use `/walmart_recovery` to inspect and act on a specific "
            "no-post reason without weakening automatic delivery."
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
            "Run `/walmart_recovery`. The audit found an exact event that currently "
            "passes the server rules without a durable public-post receipt."
        )
    if delivery_health.events_with_errors:
        return (
            "Run `/walmart_recovery` to retry the affected event under current rules. "
            "The global fanout table contains a recent error."
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
            "Run `/walmart_recovery` to inspect recent exact events. Safe retry keeps "
            f"the **{threshold}%+** threshold; the server owner may post one soft-blocked "
            "event without changing automatic settings."
        )
    if delivery_health.events_seen == 0:
        return (
            "No new exact Walmart event reached fanout in the audit window. "
            "The catalog can still be running normally; there was simply nothing "
            f"new that reached the server's **{threshold}%+** public gate."
        )
    return None


def recovery_actions_line(
    item: WalmartRecoveryItem,
    *,
    is_server_owner: bool,
) -> str:
    lines = [
        (
            "✅ **Retry current rules** — available"
            if item.can_retry_current_rules
            else "➖ **Retry current rules** — not applicable"
        ),
        (
            "✅ **Recheck exact offer** — available"
            if item.can_recheck_exact
            else "➖ **Recheck exact offer** — not applicable"
        ),
    ]
    if item.can_owner_override:
        lines.append(
            "✅ **Post once** — server owner confirmation required"
            if is_server_owner
            else "🔒 **Post once** — actual server owner only"
        )
    else:
        lines.append("⛔ **Post once** — hard proof/identity failures cannot be called verified")
    if item.can_share_manual_lead:
        lines.append(
            "✅ **Share as lead** — clearly labeled unverified; owner confirmation required"
            if is_server_owner
            else "🔒 **Share as lead** — actual server owner only"
        )
    return "\n".join(lines)


def recovery_result_text(result: WalmartRecoveryActionResult) -> str:
    prefix = "✅" if result.ok else "⚠️"
    return f"{prefix} {result.message}"


def outcome_label(outcome: str) -> str:
    labels = {
        "pending": "Waiting for fanout",
        "fanout_error": "Delivery error",
        "reserved": "Duplicate/reserved",
        "category_muted": "Muted category",
        "below_threshold": "Below threshold",
        "quality_blocked": "Exact proof/quality blocked",
        "eligible_without_post": "Eligible but not delivered",
        "invalid_snapshot": "Unreadable snapshot",
    }
    return labels.get(str(outcome), str(outcome).replace("_", " ").title())


def recovery_color(outcome: str) -> discord.Color:
    if outcome in {"eligible_without_post", "fanout_error"}:
        return discord.Color.red()
    if outcome in {"quality_blocked", "invalid_snapshot"}:
        return discord.Color.dark_orange()
    return discord.Color.orange()


def trim_component_label(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def trim_field(value: str, limit: int) -> str:
    text = str(value or "No detailed server decision has been recorded yet.")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
