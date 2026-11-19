"""Canonical ``/staff`` command group (consolidation PR 2, 2026-11-19).

Every staff tool lives under one group so the top-level surface stays at
seven: ``/staff channel``, ``/staff threshold``, ``/staff health`` and
friends replace the scattered ``/walmart setup``, ``/sniperplug_dashboard``,
``/home_depot_search`` and similar top-levels. Each subcommand delegates
to the handler that already owned the old command, so no behavior changes
here — the old names are retired in
``sniperplug/services/command_surface.py`` and pruned from the tree at
startup.

Delegation uses the same ``command.callback(cog, interaction, ...)``
pattern as the /walmart group, and every subcommand re-declares the
Manage Server gate the old command had, because delegation bypasses the
original command's checks.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.canonical_settings import (
    AUDIENCE_CHOICES,
    DASHBOARD_VIEW_CHOICES,
)
from sniperplug.services.public_posting import SUPPORTED_RETAILERS

log = logging.getLogger("sniperplug")

GROUP_DESCRIPTION = "Staff tools: server setup, health, and diagnostics in one place."

_RETAILER_LABELS = {"hp": "HP", "bestbuy": "Best Buy"}

# Source cog (by __cog_name__) + handler method for each subcommand. Every
# entry is a Command object whose ``.callback`` is the handler that owned
# the old top-level or /walmart command of the same job.
_DELEGATES: dict[str, tuple[str, str]] = {
    "channel": ("CanonicalWorkflowCog", "setup_sniperplug_here"),
    "threshold": ("CanonicalSettingsCog", "deal_threshold"),
    "categories": ("CanonicalPublicAlertsCog", "deal_categories"),
    "extreme": ("CanonicalPublicAlertsCog", "walmart_extreme_alerts"),
    "health": ("CanonicalPublicAlertsCog", "autoscan_health"),
    "active": ("ActiveDealsCog", "active_deals"),
    "recovery": ("CanonicalPublicAlertsCog", "walmart_recovery"),
    "dashboard": ("CanonicalSettingsCog", "sniperplug_dashboard"),
    "hd-search": ("HomeDepotSearchCog", "home_depot_search"),
    "hd-penny": ("HomeDepotSearchCog", "home_depot_penny_hunt"),
    "hd-stock": ("HomeDepotLocalCog", "hd_stock"),
    "test-alert": ("SniperPlugCog", "test_alert"),
}

STAFF_SUBCOMMANDS: tuple[str, ...] = tuple(_DELEGATES)


class StaffCommandCog(commands.GroupCog, group_name="staff", group_description=GROUP_DESCRIPTION):
    """One group for every staff tool."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- delegation ----------------------------------------------------

    async def _delegate(
        self,
        interaction: discord.Interaction,
        key: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Run the handler that used to own the old staff command."""

        cog_name, method_name = _DELEGATES[key]
        cog = self.bot.get_cog(cog_name)
        if cog is None:
            await self._send_error(
                interaction,
                "That staff function isn't loaded right now. Try again after the next restart.",
            )
            return
        command = getattr(cog, method_name, None)
        if command is None:
            await self._send_error(
                interaction,
                "That staff function isn't loaded right now. Try again after the next restart.",
            )
            return
        callback = getattr(command, "callback", None)
        if callback is not None:
            await callback(cog, interaction, *args, **kwargs)
        else:
            # Plain method: call it directly.
            await command(interaction, *args, **kwargs)

    @staticmethod
    async def _send_error(interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @staticmethod
    def _type_only_error(error: BaseException) -> str:
        """Error text for users: the type only, never the raw exception."""

        return type(error).__name__

    # -- server setup ----------------------------------------------------

    @app_commands.command(name="channel", description="Set (or move) this server's deal-alert channel.")
    @app_commands.describe(
        public_alerts="Allow price-checked retailer deals in this channel.",
        threshold="Smallest discount for this server. Recommended: 30-40. Omit to keep the saved threshold.",
        best_categories="Apply broad Deal Week and Walmart Cash category coverage.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(
        self,
        interaction: discord.Interaction,
        public_alerts: bool = True,
        threshold: app_commands.Range[int, 0, 95] | None = None,
        best_categories: bool = True,
    ) -> None:
        await self._delegate(
            interaction,
            "channel",
            public_alerts=public_alerts,
            threshold=threshold,
            best_categories=best_categories,
        )

    @channel.error
    async def channel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to configure SniperPlug."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug setup failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @app_commands.command(name="threshold", description="Set this server's smallest public discount.")
    @app_commands.describe(
        percent="Public channel only. 30-40 normal, 50+ stricter. 0 means no discount filter.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def threshold(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 0, 95],
    ) -> None:
        await self._delegate(interaction, "threshold", percent=percent)

    @app_commands.command(name="dashboard", description="Open SniperPlug overview, doctor, command guide, routes, or storage.")
    @app_commands.describe(
        view="Choose the dashboard page.",
        audience="Optional command-guide audience filter.",
    )
    @app_commands.choices(view=DASHBOARD_VIEW_CHOICES, audience=AUDIENCE_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dashboard(
        self,
        interaction: discord.Interaction,
        view: app_commands.Choice[str] | None = None,
        audience: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._delegate(interaction, "dashboard", view=view, audience=audience)

    @threshold.error
    @dashboard.error
    async def owner_settings_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to use owner settings."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug settings failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- server controls ---------------------------------------------------

    @app_commands.command(name="categories", description="Choose which deal categories this server receives.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def categories(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "categories")

    @app_commands.command(name="extreme", description="Turn extreme price leads on or off for this server.")
    @app_commands.describe(enabled="Receive separately labeled extreme price leads")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def extreme(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self._delegate(interaction, "extreme", enabled=enabled)

    @app_commands.command(name="health", description="Check global retailer coverage and this server's deal delivery.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def health(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "health")

    @app_commands.command(name="recovery", description="Review and recover recent Walmart deals that did not post.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def recovery(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "recovery")

    @categories.error
    @extreme.error
    @health.error
    @recovery.error
    async def server_controls_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to use these server controls."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug server controls failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @app_commands.command(name="active", description="Page through deals SniperPlug observed recently.")
    @app_commands.describe(
        retailer="Optional store filter.",
        limit="Rows per page. Max 15.",
        page="Page number to open.",
        search="Optional title/source search.",
        min_discount="Omit for the clean public-quality view; 0 shows raw cache rows.",
        sort="How to sort cached rows.",
    )
    @app_commands.choices(
        retailer=[
            app_commands.Choice(name=_RETAILER_LABELS.get(key, key.replace("_", " ").title()), value=key)
            for key in sorted(SUPPORTED_RETAILERS)
        ],
        sort=[
            app_commands.Choice(name="Most recent", value="recent"),
            app_commands.Choice(name="Biggest discount", value="discount"),
            app_commands.Choice(name="Highest score", value="score"),
            app_commands.Choice(name="Lowest price", value="price_low"),
            app_commands.Choice(name="Highest price", value="price_high"),
        ],
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active(
        self,
        interaction: discord.Interaction,
        retailer: str | None = None,
        limit: app_commands.Range[int, 1, 15] = 10,
        page: app_commands.Range[int, 1, 999] = 1,
        search: str | None = None,
        min_discount: app_commands.Range[int, 0, 95] | None = None,
        sort: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._delegate(
            interaction,
            "active",
            retailer=retailer,
            limit=limit,
            page=page,
            search=search,
            min_discount=min_discount,
            sort=sort,
        )

    @active.error
    async def active_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to use these server controls."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug server controls failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- Home Depot --------------------------------------------------------

    @app_commands.command(name="hd-search", description="Manually search Home Depot through SerpApi with quota protection.")
    @app_commands.describe(
        query="Product search, like milwaukee drill, vanity, faucet, ceiling fan.",
        store_id="Optional Home Depot store ID.",
        zip_code="Optional delivery/local ZIP.",
        page="Page to scan. Page 2+ uses another SerpApi credit.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_search(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None = None,
        zip_code: str | None = None,
        page: app_commands.Range[int, 1, 5] = 1,
    ) -> None:
        await self._delegate(
            interaction,
            "hd-search",
            query=query,
            store_id=store_id,
            zip_code=zip_code,
            page=page,
        )

    @app_commands.command(name="hd-penny", description="Manually hunt Home Depot penny/clearance candidates through SerpApi.")
    @app_commands.describe(
        query="Targeted query, like faucet, vanity, milwaukee, ryobi, ceiling fan.",
        store_id="Optional Home Depot store ID. ZIP alone is enough for a local search.",
        zip_code="Recommended local ZIP. Used as the local anchor when store ID is unknown.",
        page="Page to scan. Page 2+ uses another SerpApi credit.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_penny(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None = None,
        zip_code: str | None = None,
        page: app_commands.Range[int, 1, 5] = 1,
    ) -> None:
        await self._delegate(
            interaction,
            "hd-penny",
            query=query,
            store_id=store_id,
            zip_code=zip_code,
            page=page,
        )

    @app_commands.command(name="hd-stock", description="Check a Home Depot SKU near a ZIP with local store selection.")
    @app_commands.describe(
        sku="SKU / Internet number.",
        zip_code="ZIP code to search around.",
        store_id="Optional Home Depot store ID.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_stock(
        self,
        interaction: discord.Interaction,
        sku: str,
        zip_code: str,
        store_id: str | None = None,
    ) -> None:
        await self._delegate(
            interaction,
            "hd-stock",
            sku=sku,
            zip_code=zip_code,
            store_id=store_id,
        )

    @hd_search.error
    @hd_penny.error
    @hd_stock.error
    async def home_depot_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to use Home Depot tools."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Home Depot tool hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- test alert ----------------------------------------------------------

    @app_commands.command(name="test-alert", description="Post a realistic SniperPlug test deal alert.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_alert(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "test-alert")

    @test_alert.error
    async def test_alert_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to use this SniperPlug admin command."
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            message = (
                "Discord blocked SniperPlug with **403 Missing Access**. "
                "Check the configured alert channel and give the bot View Channel, Send Messages, Embed Links, and Read Message History."
            )
        else:
            message = f"SniperPlug hit an error: `{self._type_only_error(error)}`"
        await self._send_error(interaction, message)
