"""Canonical ``/walmart`` command group.

Every Walmart slash command lives under one group so the top-level surface
stays small: ``/walmart check``, ``/walmart find``, ``/walmart hunt`` and
friends. Each subcommand delegates to the handler that already owned the
old top-level command, so no deal-pipeline behavior changes here — the old
names are retired in ``sniperplug/services/command_surface.py`` and pruned
from the tree at startup.

Delegation uses the same ``command.callback(cog, interaction, ...)`` pattern
the test suite already uses (see ``test_single_threshold_and_scan_locks_b4``).
The group's own ``@app_commands.checks`` decorators enforce the same
permissions the old commands had; the menu (``/walmart menu``) enforces
those same gates itself before running a function in place, because menu
interactions bypass command checks.

``/walmart menu`` is the interactive front door for resellers: an embed
plus four member buttons (Check a deal, Find deals, Hunt deals, My
alerts) and, for staff only, a select with the server-setup functions.
Five subcommands live only as slash commands now — recovery, history,
trace, probe, feedback-reset — and appear in no menu surface.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.active_deal_recheck import ActiveDealRecheckCog
from sniperplug.cogs.auto_discovery import DISCOVERY_COVERAGE_CHOICES
from sniperplug.cogs.dm_deal_alerts import ACTION_CHOICES, MODE_CHOICES
from sniperplug.services.deal_threshold_settings import (
    DEFAULT_STARTING_DEAL_PERCENT,
    set_starting_deal_percent,
)
from sniperplug.services.dm_deal_alerts import (
    get_dm_deal_alert_preference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.menu_dashboard_state import (
    MenuDashboardState,
    load_menu_dashboard_state,
)
from sniperplug.services.public_posting import SUPPORTED_RETAILERS
from sniperplug.services.walmart_extreme_price_fanout import (
    extreme_alerts_enabled,
    set_extreme_alerts_enabled,
)

log = logging.getLogger("sniperplug")

GROUP_DESCRIPTION = "Walmart deals: check, find, hunt, alerts, and server setup in one place."

# Inline dashboard controls (menu redesign, 2026-11-17). The threshold
# stepper writes through the same setter /walmart threshold uses, clamped
# to the 10-90 band the stepper shows; the setter's own normalizer clamps
# 0-95, so a value the stepper can produce is always accepted as-is.
THRESHOLD_STEP_PERCENT = 5
THRESHOLD_STEP_MIN = 10
THRESHOLD_STEP_MAX = 90

# Walmart brand blue, shared with the hub's Walmart category accent.
_WALMART_BLUE = discord.Color(0x0071DC)

_RETAILER_LABELS = {"hp": "HP", "bestbuy": "Best Buy"}

# Source cog (by __cog_name__) + method for each subcommand. ``deal_trace``
# is a plain method on SniperPlugCog (its slash decorator was removed when
# the command moved here); every other entry is a Command object whose
# ``.callback`` is the handler.
#
# check/find resolve through VerifiedDealScannerCog, NOT DealScannerCog:
# the live bot only adds the verified subclass (bot.py), so naming the base
# class makes get_cog return None and every call fails with "isn't loaded
# right now". The methods are inherited unchanged from the base class.
_DELEGATES: dict[str, tuple[str, str]] = {
    "check": ("VerifiedDealScannerCog", "check_deal"),
    "find": ("VerifiedDealScannerCog", "deals"),
    "hunt": ("VerifiedDealScannerCog", "hunt"),
    "discover": ("AutoDiscoveryCog", "discover"),
    # scan-now resolves by loaded instance, NOT by the cog name below: three
    # modules each define a class literally named AutoScanRunnerCog (legacy,
    # native/resilient, global) and only the global one is loaded, so a
    # name lookup breaks silently the moment the global class is renamed.
    # _delegate resolves it by module instead (see _SCAN_NOW_RUNNER_MODULE);
    # the name here is kept only for the static delegate audit.
    "scan-now": ("AutoScanRunnerCog", "autoscan_now"),
    "health": ("CanonicalPublicAlertsCog", "autoscan_health"),
    "threshold": ("CanonicalSettingsCog", "deal_threshold"),
    "categories": ("CanonicalPublicAlertsCog", "deal_categories"),
    "extreme": ("CanonicalPublicAlertsCog", "walmart_extreme_alerts"),
    "recovery": ("CanonicalPublicAlertsCog", "walmart_recovery"),
    "trace": ("SniperPlugCog", "deal_trace"),
    "my-alerts": ("DmDealAlertsCog", "dm_deals"),
    "feedback": ("DealFeedbackAdminCog", "feedback_learning_status"),
    "feedback-reset": ("DealFeedbackAdminCog", "feedback_learning_reset"),
    "active": ("ActiveDealsCog", "active_deals"),
    "history": ("ActiveDealHistoryCog", "active_deal_history"),
    "setup": ("CanonicalWorkflowCog", "setup_sniperplug_here"),
    # Restored diagnostics (audit SS3 item 12): walmart_api_probe lives on
    # DealScannerCog and is inherited by the loaded VerifiedDealScannerCog.
    "probe": ("VerifiedDealScannerCog", "walmart_api_probe"),
}

# scan-now resolves by loaded instance, not by cog name (see the note on the
# _DELEGATES entry above).
_SCAN_NOW_RUNNER_MODULE = "sniperplug.cogs.global_auto_scan_runner"

# Subcommands restored from never-loaded cogs, served by a locally held
# instance instead of a bot-loaded cog: key -> (attribute on this cog,
# method name). ActiveDealRecheckCog is never added to the live bot, so the
# group holds its own instance; the cog has no listeners or cog_load, and
# its handlers only need bot.db plus the provider registry.
_LOCAL_DELEGATES: dict[str, tuple[str, str]] = {
    "recheck": ("_recheck_cog", "active_deal_recheck"),
}

# The 19 functions, in subcommand order (the 20th subcommand is the menu).
WALMART_SUBCOMMANDS: tuple[str, ...] = tuple(_DELEGATES) + tuple(_LOCAL_DELEGATES)

# Who sees what in the menu (reseller rebuild, 2026-11-17). Members get
# four buttons; staff get those plus the staff select and the threshold /
# extreme controls. Five subcommands left the menu entirely — recovery,
# history, trace, probe, feedback-reset — and live only as /walmart
# subcommands (see MENU_REMOVED_KEYS).
MENU_MEMBER_KEYS: tuple[str, ...] = ("check", "find", "hunt", "my-alerts")
MENU_STAFF_KEYS: tuple[str, ...] = (
    "setup",
    "threshold",
    "categories",
    "extreme",
    "health",
    "feedback",
    "discover",
    "scan-now",
    "recheck",
    "active",
)
MENU_REMOVED_KEYS = frozenset({"recovery", "history", "trace", "probe", "feedback-reset"})

MENU_MEMBER_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("🔍 Find deals", ("check", "find", "hunt")),
    ("🔔 Your alerts", ("my-alerts",)),
)
MENU_STAFF_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("🛠️ Server setup", ("setup", "threshold", "categories", "extreme", "feedback")),
    ("🏥 Health & more", ("health", "discover", "scan-now", "recheck", "active")),
)

# Subcommands the menu runs in place (no required input). Everything else
# either opens a modal (check/find) or shows guidance with the exact
# subcommand to run (trace/threshold/extreme/feedback-reset/recheck).
_MENU_RUN_IN_PLACE = frozenset(
    {
        "hunt",
        "health",
        "categories",
        "recovery",
        "feedback",
        "discover",
        "scan-now",
        "active",
        "history",
        "setup",
        "probe",
        "my-alerts",
    }
)
_MENU_MODAL = frozenset({"check", "find"})
_MENU_GUIDANCE = frozenset({"trace", "threshold", "extreme", "feedback-reset", "recheck"})

# Subcommands that require Manage Server (same gate as the old commands).
# The menu enforces this itself before running one in place or opening a
# modal, because menu interactions bypass command checks. "check" is NOT
# here: it has its own gate (Deal Finder role OR Manage Server, see
# _can_check_deal).
_MENU_MANAGE_GUILD = frozenset(
    {
        "health",
        "threshold",
        "categories",
        "extreme",
        "recovery",
        "trace",
        "feedback",
        "feedback-reset",
        "active",
        "history",
        "setup",
        "scan-now",
        "recheck",
        "probe",
    }
)


# Deal Finder role (The 420 Lobby, "🔍 Deal Finder", 6 members 2026-11-17).
# The check gate is by role ID, so in a guild without that role it simply
# never matches and Manage Server is the only way in.
DEAL_FINDER_ROLE_ID = 1517181991412302025


def _has_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
    return bool(permissions is not None and getattr(permissions, "manage_guild", False))


def _can_check_deal(interaction: discord.Interaction) -> bool:
    """Deal Finder role OR Manage Server. Guards DMs / missing roles."""
    if _has_manage_guild(interaction):
        return True
    roles = getattr(getattr(interaction, "user", None), "roles", None)
    if not roles:
        return False
    try:
        return any(getattr(role, "id", None) == DEAL_FINDER_ROLE_ID for role in roles)
    except TypeError:
        return False


class WalmartCommandCog(commands.GroupCog, group_name="walmart", group_description=GROUP_DESCRIPTION):
    """One group for every Walmart command, plus the interactive menu."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._recheck_cog = ActiveDealRecheckCog(bot)

    # -- delegation ----------------------------------------------------

    async def _delegate(
        self,
        interaction: discord.Interaction,
        key: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Run the handler that used to own the old top-level command."""

        if key in _LOCAL_DELEGATES:
            attr_name, method_name = _LOCAL_DELEGATES[key]
            cog = getattr(self, attr_name)
        elif key == "scan-now":
            # Resolved by loaded instance, not by cog name: the class name
            # AutoScanRunnerCog exists in three runner modules.
            method_name = _DELEGATES[key][1]
            cog = self._find_global_scan_runner()
        else:
            cog_name, method_name = _DELEGATES[key]
            cog = self.bot.get_cog(cog_name)
        if cog is None:
            await self._send_error(
                interaction,
                "That Walmart function isn't loaded right now. Try again after the next restart.",
            )
            return
        command = getattr(cog, method_name, None)
        if command is None:
            await self._send_error(
                interaction,
                "That Walmart function isn't loaded right now. Try again after the next restart.",
            )
            return
        callback = getattr(command, "callback", None)
        if callback is not None:
            await callback(cog, interaction, *args, **kwargs)
        else:
            # Plain method (deal_trace): call it directly.
            await command(interaction, *args, **kwargs)

    def _find_global_scan_runner(self):
        """The loaded global autoscan runner, found by module, not by the
        class name it shares with the legacy/native/resilient runners."""

        cogs = getattr(self.bot, "cogs", None) or {}
        try:
            candidates = list(cogs.values())
        except AttributeError:
            candidates = list(cogs)
        for candidate in candidates:
            if (
                type(candidate).__module__ == _SCAN_NOW_RUNNER_MODULE
                and hasattr(candidate, "autoscan_now")
            ):
                return candidate
        return None

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

    # -- find deals ----------------------------------------------------

    @app_commands.command(name="check", description="Check one Walmart product link or item ID directly, even when search never surfaces it.")
    @app_commands.describe(link="Walmart product link or bare item ID. Example: https://www.walmart.com/ip/AT-T-Apple-iPhone-11-64GB-Green/425031866 or 425031866")
    @app_commands.check(_can_check_deal)
    async def check(self, interaction: discord.Interaction, link: str) -> None:
        await self._delegate(interaction, "check", link=link)

    @check.error
    async def check_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need the 🔍 Deal Finder role or Manage Server to check deals."
            if isinstance(error, (app_commands.CheckFailure, app_commands.MissingPermissions))
            else f"Direct deal check hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @app_commands.command(name="find", description="Find deals the easy way. Just type what you want.")
    @app_commands.describe(search="What are you shopping for? Example: monitor, lego, tide, patio set, air fryer.")
    async def find(self, interaction: discord.Interaction, search: str) -> None:
        # A blank search reaches the provider scan as an empty query; reject
        # it here with the same friendly tone as the check parse failure.
        if not str(search or "").strip():
            await interaction.response.send_message(
                "Type what you're looking for — for example `oled tv`, `lego`, or `detergent`.",
                ephemeral=True,
            )
            return
        await self._delegate(interaction, "find", search=search)

    @find.error
    async def find_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await self._send_error(interaction, f"Deal search hit an error: `{self._type_only_error(error)}`")

    @app_commands.command(name="hunt", description="Pick a Walmart category and let SniperPlug hunt verified deals.")
    async def hunt(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "hunt")

    @hunt.error
    async def hunt_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        # The live hunt override never had an error handler (the only one
        # was attached to the shadowed base command), so failures surfaced
        # nowhere friendly. This one matches the deals pattern.
        await self._send_error(interaction, f"Deal hunt hit an error: `{self._type_only_error(error)}`")

    # -- discovery -----------------------------------------------------

    @app_commands.command(name="discover", description="Start a broad exact-verified Walmart catalog discovery job.")
    @app_commands.describe(
        coverage="Quick: 16 routes. Deep: 64. Full: every route in a durable background job. Default: Deep.",
        public_posts="Also publish verified finds to the server deal channel. Off keeps this run private.",
        max_public_posts="When public posts are on, cap how many verified deals can be published.",
    )
    @app_commands.choices(coverage=DISCOVERY_COVERAGE_CHOICES)
    async def discover(
        self,
        interaction: discord.Interaction,
        coverage: app_commands.Choice[str] | None = None,
        public_posts: bool = False,
        max_public_posts: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        await self._delegate(
            interaction,
            "discover",
            coverage=coverage,
            public_posts=public_posts,
            max_public_posts=max_public_posts,
        )

    @discover.error
    async def discover_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await self._send_error(interaction, f"Discovery hit an error: `{self._type_only_error(error)}`")

    @app_commands.command(name="scan-now", description="Run the Walmart auto-scan now and show why it did or did not post.")
    @app_commands.describe(force="Bypass interval/daily gate for this manual test. Public alerts still must be configured.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan_now(
        self,
        interaction: discord.Interaction,
        force: bool = True,
    ) -> None:
        await self._delegate(interaction, "scan-now", force=force)

    @scan_now.error
    async def scan_now_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to run a manual auto-scan."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Manual auto-scan hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- recheck & diagnostics (restored, audit SS3 item 12) -------------

    @app_commands.command(name="recheck", description="Recheck one cached Walmart deal through the official item-detail provider.")
    @app_commands.describe(search="Part of the cached title, Walmart URL, or exact active-cache key.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def recheck(self, interaction: discord.Interaction, search: str) -> None:
        await self._delegate(interaction, "recheck", search=search)

    @recheck.error
    async def recheck_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to recheck cached deals."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Deal recheck hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @app_commands.command(name="probe", description="Owner diagnostic: check Walmart API promo proof paths.")
    @app_commands.describe(
        query="Product/category to inspect. Example: detergent, personal care, baby.",
        max_results="Small bounded probe size.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def probe(
        self,
        interaction: discord.Interaction,
        query: str = "detergent",
        max_results: app_commands.Range[int, 1, 8] = 3,
    ) -> None:
        await self._delegate(interaction, "probe", query=query, max_results=max_results)

    @probe.error
    async def probe_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to run the Walmart API probe."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Walmart API probe hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- server health & setup ------------------------------------------

    @app_commands.command(name="health", description="Check global retailer coverage and this server's deal delivery.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def health(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "health")

    @app_commands.command(name="threshold", description="Set this server's public deal-channel minimum markdown.")
    @app_commands.describe(
        percent="Public channel only. 30-40 normal, 50+ stricter. 0 means no markdown filter.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def threshold(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 0, 95],
    ) -> None:
        await self._delegate(interaction, "threshold", percent=percent)

    @app_commands.command(name="categories", description="Choose which verified deal categories this server receives.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def categories(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "categories")

    @app_commands.command(name="extreme", description="Turn exact-price Walmart leads on or off for this server.")
    @app_commands.describe(enabled="Receive separately labeled extreme exact-price leads")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def extreme(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self._delegate(interaction, "extreme", enabled=enabled)

    @app_commands.command(name="recovery", description="Review and recover recent Walmart deals that did not post.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def recovery(self, interaction: discord.Interaction) -> None:
        await self._delegate(interaction, "recovery")

    @health.error
    @categories.error
    @recovery.error
    @extreme.error
    async def server_controls_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to use these server controls."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug server controls failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @threshold.error
    async def threshold_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to use owner settings."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug settings failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- evidence -------------------------------------------------------

    @app_commands.command(name="trace", description="Read-only evidence trace for one exact Walmart item.")
    @app_commands.describe(item_id="Digits only — the number after /ip/ in the link, not the full link.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trace(self, interaction: discord.Interaction, item_id: str) -> None:
        await self._delegate(interaction, "trace", item_id=item_id)

    @trace.error
    async def trace_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
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

    # -- personal alerts -------------------------------------------------

    @app_commands.command(name="my-alerts", description="Set personal exact-verified deal alerts in your DMs.")
    @app_commands.describe(
        action="Open the menu, inspect, test, pause, or update your personal alerts.",
        mode="Smart adapts quality rules to price; All and Custom use hard filters.",
        min_discount="Lowest exact Walmart markdown percentage you want.",
        max_price="Do not DM products above this current price, including flips.",
        min_score="Minimum Sniper score from 0 to 250.",
        min_savings="Minimum exact dollar savings from Walmart's was price.",
        categories="Optional hard allowlist. Leave empty to keep all categories eligible.",
        favorite_categories="Prioritize interests without excluding other great deals: tech, gaming, PC, smart home.",
        unfavorite_categories="Remove categories from your personal favorites.",
        product_interests="Add specific products: portable chargers, NeeDoh, Pokemon TCG, cologne, LEGO, and more.",
        remove_interests="Remove specific product interests from your personal DMs.",
        brands="Add brand filters such as Anker, Versace, Dior, YSL, Belkin, or LEGO.",
        remove_brands="Remove brands from your personal DM filter.",
        keywords="Advanced comma list: at least one of these words must match.",
        exclude="Comma list of words that always block a product, including flips.",
        mute_categories="Hide categories from normal DMs, such as baby, toys, pets, or beauty.",
        unmute_categories="Restore personally muted categories, such as baby or toys.",
        flip_alerts="Allow exceptional price-error/resell alerts across category mutes.",
        flip_min_profit="Minimum conservative estimated net profit for a flip alert.",
        daily_cap="Daily DM limit: 1-100, or type unlimited for no cap.",
    )
    @app_commands.choices(action=ACTION_CHOICES, mode=MODE_CHOICES)
    async def my_alerts(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        mode: app_commands.Choice[str] | None = None,
        min_discount: app_commands.Range[int, 0, 95] | None = None,
        max_price: app_commands.Range[float, 0.01, 100000.0] | None = None,
        min_score: app_commands.Range[int, 0, 250] | None = None,
        min_savings: app_commands.Range[float, 0.0, 100000.0] | None = None,
        categories: app_commands.Range[str, 1, 300] | None = None,
        favorite_categories: app_commands.Range[str, 1, 300] | None = None,
        unfavorite_categories: app_commands.Range[str, 1, 300] | None = None,
        product_interests: app_commands.Range[str, 1, 400] | None = None,
        remove_interests: app_commands.Range[str, 1, 400] | None = None,
        brands: app_commands.Range[str, 1, 400] | None = None,
        remove_brands: app_commands.Range[str, 1, 400] | None = None,
        keywords: app_commands.Range[str, 1, 300] | None = None,
        exclude: app_commands.Range[str, 1, 300] | None = None,
        mute_categories: app_commands.Range[str, 1, 300] | None = None,
        unmute_categories: app_commands.Range[str, 1, 300] | None = None,
        flip_alerts: bool | None = None,
        flip_min_profit: app_commands.Range[float, 10.0, 100000.0] | None = None,
        daily_cap: app_commands.Range[str, 1, 12] | None = None,
    ) -> None:
        await self._delegate(
            interaction,
            "my-alerts",
            action=action,
            mode=mode,
            min_discount=min_discount,
            max_price=max_price,
            min_score=min_score,
            min_savings=min_savings,
            categories=categories,
            favorite_categories=favorite_categories,
            unfavorite_categories=unfavorite_categories,
            product_interests=product_interests,
            remove_interests=remove_interests,
            brands=brands,
            remove_brands=remove_brands,
            keywords=keywords,
            exclude=exclude,
            mute_categories=mute_categories,
            unmute_categories=unmute_categories,
            flip_alerts=flip_alerts,
            flip_min_profit=flip_min_profit,
            daily_cap=daily_cap,
        )

    @my_alerts.error
    async def my_alerts_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await self._send_error(
            interaction,
            "Personal alert setup failed safely. No settings were changed. "
            f"Error: `{self._type_only_error(error)}`",
        )

    # -- feedback learning -------------------------------------------------

    @app_commands.command(name="feedback", description="Show what SniperPlug learned from deal feedback buttons.")
    @app_commands.describe(limit="How many boosted/penalized products and brands to show.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def feedback(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 3, 10] = 5,
    ) -> None:
        await self._delegate(interaction, "feedback", limit=limit)

    @feedback.error
    async def feedback_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to view feedback learning."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Feedback learning status hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @app_commands.command(name="feedback-reset", description="Reset SniperPlug feedback learning for this server.")
    @app_commands.describe(
        scope="What to clear: all, products, brands, raw feedback events, or active votes.",
        confirm="Must be true so this cannot be clicked by accident.",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Everything", value="all"),
            app_commands.Choice(name="Product learning only", value="products"),
            app_commands.Choice(name="Brand learning only", value="brands"),
            app_commands.Choice(name="Raw feedback click history only", value="events"),
            app_commands.Choice(name="Active vote ledger only", value="votes"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def feedback_reset(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
        confirm: bool = False,
    ) -> None:
        await self._delegate(interaction, "feedback-reset", scope=scope, confirm=confirm)

    @feedback_reset.error
    async def feedback_reset_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to reset feedback learning."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Feedback learning reset hit an error: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- recent deals -------------------------------------------------------

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
        # The old command had no error handler at all; a non-manager got no
        # friendly message. This matches the canonical server-controls one.
        message = (
            "You need **Manage Server** permission to use these server controls."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug server controls failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    @app_commands.command(name="history", description="Review cached deal lifecycle changes or Walmart recheck attempts.")
    @app_commands.describe(
        view="Choose lifecycle changes or the Walmart recheck audit.",
        retailer="Optional store filter for lifecycle history.",
        search="Optional title, item key, status, event, or source search.",
        limit="How many history rows to show. Maximum 25.",
    )
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Lifecycle changes", value="lifecycle"),
            app_commands.Choice(name="Walmart recheck audit", value="rechecks"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def history(
        self,
        interaction: discord.Interaction,
        view: app_commands.Choice[str] | None = None,
        retailer: str | None = None,
        search: str | None = None,
        limit: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        await self._delegate(
            interaction,
            "history",
            view=view,
            retailer=retailer,
            search=search,
            limit=limit,
        )

    @history.error
    async def history_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        # Same as /walmart active: the old command had no error handler.
        message = (
            "You need **Manage Server** permission to use these server controls."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug server controls failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- setup ---------------------------------------------------------------

    @app_commands.command(name="setup", description="Set (or move) this server's deal-alert channel.")
    @app_commands.describe(
        public_alerts="Allow exact-verified retailer deals in this channel.",
        threshold="Minimum exact markdown for this server. Recommended: 30-40. Omit to keep the saved threshold.",
        best_categories="Apply broad Deal Week and Walmart Cash category coverage.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        public_alerts: bool = True,
        threshold: app_commands.Range[int, 0, 95] | None = None,
        best_categories: bool = True,
    ) -> None:
        await self._delegate(
            interaction,
            "setup",
            public_alerts=public_alerts,
            threshold=threshold,
            best_categories=best_categories,
        )

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to configure SniperPlug."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug setup failed safely: `{self._type_only_error(error)}`"
        )
        await self._send_error(interaction, message)

    # -- menu ------------------------------------------------------------------

    @app_commands.command(name="menu", description="Open the Walmart menu: every Walmart function in one place.")
    async def menu(self, interaction: discord.Interaction) -> None:
        # Defer FIRST: the dashboard snapshot reads Turso, and prod lock
        # waits of 2.5-7.5s blow past Discord's 3s interaction window
        # (Unknown interaction 10062, live 2026-11-17). The snapshot
        # loads after the defer, then the menu arrives as a follow-up.
        await interaction.response.defer(ephemeral=True)
        state: MenuDashboardState | None = None
        try:
            state = await load_menu_dashboard_state(
                self.bot, interaction.guild_id, interaction.user.id
            )
        except Exception:
            # The loader never raises by contract; this only guards the
            # call itself. A failed read renders the fallback line.
            state = None
        # The split is decided once, from this interaction, and handed to
        # the embed and the view together — never re-derived later.
        manage_guild = _has_manage_guild(interaction)
        can_check = _can_check_deal(interaction)
        await interaction.followup.send(
            embed=build_walmart_menu_embed(
                state, manage_guild=manage_guild, can_check=can_check
            ),
            view=WalmartMenuView(
                self, state, manage_guild=manage_guild, can_check=can_check
            ),
            ephemeral=True,
        )


# -- menu --------------------------------------------------------------------

# key -> (label, short description, exact usage shown when it needs input)
MENU_FUNCTIONS: dict[str, tuple[str, str, str]] = {
    "check": ("Check a deal", "Check one link or item ID, even when search misses it.", "/walmart check link:<Walmart link or item ID>"),
    "find": ("Find deals", "Search Walmart deals by typing what you want.", "/walmart find search:<what you want>"),
    "hunt": ("Hunt deals", "Pick a category and hunt verified deals.", "/walmart hunt"),
    "discover": ("Discover", "Run a broad catalog sweep (Quick, Deep, or Full).", "/walmart discover"),
    "scan-now": ("Scan now", "Run the Walmart auto-scan now and show why it did or did not post.", "/walmart scan-now"),
    "recheck": ("Recheck a deal", "Recheck one cached Walmart deal through the official provider.", "/walmart recheck search:<title, URL, or cache key>"),
    "probe": ("API probe", "Owner diagnostic: check Walmart API promo proof paths.", "/walmart probe"),
    "my-alerts": ("My alerts", "Your personal DM alerts: menu, filters, pause, delete.", "/walmart my-alerts action:Open personalization menu"),
    "setup": ("Setup", "Set (or move) this server's deal-alert channel.", "/walmart setup"),
    "threshold": ("Threshold", "Set the server's minimum markdown for public deals.", "/walmart threshold percent:35"),
    "categories": ("Categories", "Choose which deal categories this server receives.", "/walmart categories"),
    "extreme": ("Extreme leads", "Turn exact-price leads with no verified price on or off.", "/walmart extreme enabled:true"),
    "health": ("Health", "Coverage and delivery check for this server.", "/walmart health"),
    "recovery": ("Recovery", "Review and recover deals that never posted.", "/walmart recovery"),
    "active": ("Active deals", "Page through deals seen recently.", "/walmart active"),
    "history": ("History", "Lifecycle changes and recheck attempts.", "/walmart history"),
    "trace": ("Trace an item", "Evidence trace for one exact Walmart item.", "/walmart trace item_id:425031866"),
    "feedback": ("Feedback", "What the bot learned from feedback buttons.", "/walmart feedback"),
    "feedback-reset": ("Reset feedback", "Clear feedback learning for this server.", "/walmart feedback-reset scope:all confirm:true"),
}

_MENU_EMOJIS = {
    "check": "🔍",
    "find": "🔎",
    "hunt": "🎯",
    "discover": "🗺️",
    "scan-now": "▶️",
    "recheck": "🔄",
    "probe": "🧪",
    "my-alerts": "🔔",
    "setup": "🛠️",
    "threshold": "📊",
    "categories": "🏷️",
    "extreme": "⚡",
    "health": "🏥",
    "recovery": "🛟",
    "active": "📋",
    "history": "🕘",
    "trace": "🔬",
    "feedback": "🧠",
    "feedback-reset": "🗑️",
}


def _dashboard_color(state: MenuDashboardState | None) -> discord.Color:
    """Embed color for the Walmart dashboard.

    No snapshot (legacy callers that never asked for state): Walmart blue.
    With a snapshot, the health-embed color language: green = ready,
    orange = degraded / needs attention, red = down / not configured.
    """
    if state is None:
        return _WALMART_BLUE
    if not state.state_available:
        return discord.Color.orange()
    if state.provider_status in ("ERROR", "DISABLED"):
        return discord.Color.red()
    if state.provider_status == "READY" and state.delivery_ready:
        return discord.Color.green()
    return discord.Color.orange()


def _provider_status_emoji(status: str) -> str:
    return {"READY": "🟢", "ERROR": "🔴", "DISABLED": "🔴"}.get(status, "🟠")


def _format_deal_line(deal: dict) -> str:
    title = str(deal.get("title") or "Untitled deal").strip() or "Untitled deal"
    if len(title) > 60:
        title = title[:57] + "…"
    bits: list[str] = []
    price = deal.get("price")
    if isinstance(price, (int, float)):
        bits.append(f"${price:,.2f}")
    discount = deal.get("discount")
    if isinstance(discount, (int, float)):
        bits.append(f"{discount:g}% off")
    suffix = f" — {' · '.join(bits)}" if bits else ""
    return f"• {title}{suffix}"


def _status_field_value(state: MenuDashboardState, notice: str | None) -> str:
    """The status strip: live state at the top, or the fallback line.

    The snapshot's active_deals + deals_today are the last-scan / recent
    summary — the snapshot carries no scan timestamp, so none is invented.
    HP and Target are off by design here, never red: this menu is Walmart
    only.
    """
    if not state.state_available:
        lines = ["Live state unavailable — the function list still works."]
    else:
        lines = [
            f"{_provider_status_emoji(state.provider_status)} Walmart provider: **{state.provider_status}** — {state.provider_message}",
            (
                "✅ Delivery ready."
                if state.delivery_ready
                else f"⚠️ {state.delivery_summary}"
            ),
            f"Public threshold: **{state.threshold_percent}%**",
            f"Extreme leads: **{'ON' if state.extreme_enabled else 'OFF'}** · {state.extreme_recent} recent in the last 15 min",
        ]
        if state.deals_today is not None:
            lines.append(
                f"Deals posted today: **{state.deals_today}** · Active deals: **{state.active_deal_count}**"
            )
        else:
            lines.append(
                f"Active deals: **{state.active_deal_count}** (deals-today count unavailable — showing active deals instead)"
            )
        if state.active_deals:
            lines.append("Recent deals:")
            lines.extend(_format_deal_line(deal) for deal in state.active_deals[:3])
        else:
            lines.append("Recent deals: none active right now.")
        dm_detail = ""
        if state.dm_threshold is not None:
            cap = (
                "no daily cap"
                if state.dm_daily_cap is None
                else f"cap {state.dm_daily_cap}/day"
            )
            dm_detail = f" (min {state.dm_threshold}% · {cap})"
        lines.append(
            f"Your DM alerts: **{'ON' if state.dm_enabled else 'OFF'}**{dm_detail}"
        )
        lines.append("HP and Target: off by design — this menu is Walmart only.")
    if notice:
        lines.append("")
        lines.append(notice)
    return "\n".join(lines)


def _menu_sections(
    *, manage_guild: bool, can_check: bool
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Embed sections for this opener: members see Find deals + Your
    alerts (Check only when they can check); staff also see the two
    staff sections. The five removed keys appear nowhere."""
    find_keys = (("check",) if can_check else ()) + ("find", "hunt")
    sections: list[tuple[str, tuple[str, ...]]] = [
        ("🔍 Find deals", find_keys),
        ("🔔 Your alerts", ("my-alerts",)),
    ]
    if manage_guild:
        sections.extend(MENU_STAFF_SECTIONS)
    return tuple(sections)


def build_walmart_menu_embed(
    state: MenuDashboardState | None = None,
    *,
    notice: str | None = None,
    can_check: bool = False,
    manage_guild: bool = False,
) -> discord.Embed:
    """The Walmart dashboard: live state at the top, then the functions
    this opener can actually use.

    ``state`` is the ``MenuDashboardState`` snapshot from
    ``load_menu_dashboard_state``. The no-argument call keeps the old
    behavior (Walmart blue, fallback status line) for callers that never
    asked for state, e.g. the unified hub — it renders the member view.
    """

    snapshot = state if state is not None else MenuDashboardState()
    embed = discord.Embed(
        title="🛒 Walmart — one menu for everything",
        description="Pick a function below, or use the quick buttons. Everything Walmart lives under `/walmart`.",
        color=_dashboard_color(state),
    )
    embed.add_field(
        name="📊 Live status",
        value=_status_field_value(snapshot, notice),
        inline=False,
    )
    for section, keys in _menu_sections(manage_guild=manage_guild, can_check=can_check):
        lines = [
            f"`/walmart {key}` — {MENU_FUNCTIONS[key][1]}"
            for key in keys
        ]
        embed.add_field(name=section, value="\n".join(lines), inline=False)
    if can_check:
        footer = "Admin functions need Manage Server. Personal alerts work for everyone."
    else:
        footer = "Deal checks need the 🔍 Deal Finder role — ask staff. Admin functions need Manage Server."
    embed.set_footer(text=footer)
    return embed


class WalmartMenuView(discord.ui.View):
    """Ephemeral menu hub. One tap runs a function or shows how to run it.

    Dashboard controls (threshold stepper, extreme toggle, DM toggle,
    Refresh) act in place: they write through the same setters the slash
    commands use, then re-render this same message with fresh state. A
    failed write shows the existing ephemeral error pattern, never a
    broken embed.
    """

    def __init__(
        self,
        cog: "WalmartCommandCog",
        state: MenuDashboardState | None = None,
        *,
        manage_guild: bool = False,
        can_check: bool = False,
    ) -> None:
        """Member view by default: Find / Hunt deals / My alerts + DM +
        Refresh. Staff openers (``manage_guild``) also get the staff
        select, the threshold stepper and the extreme toggle; openers
        who can check (Deal Finder role or Manage Server) also get the
        Check button. The flags are decided once by the caller, from
        the opening interaction, and stored — never re-derived."""
        super().__init__(timeout=300)
        self.cog = cog
        self.state = state
        self.manage_guild = manage_guild
        self.can_check = can_check
        self._notice: str | None = None
        if can_check:
            self.add_item(WalmartMenuCheckButton(cog))
        self.add_item(WalmartMenuFindButton(cog))
        self.add_item(WalmartMenuHuntButton(cog))
        self.add_item(WalmartMenuMyAlertsButton(cog))
        self._threshold_down = WalmartThresholdDownButton(cog)
        self._threshold_value = WalmartThresholdValueButton(cog)
        self._threshold_up = WalmartThresholdUpButton(cog)
        self._extreme_toggle = WalmartExtremeToggleButton(cog)
        self._dm_toggle = WalmartDmToggleButton(cog)
        self._refresh_button = WalmartRefreshButton(cog)
        if manage_guild:
            self.add_item(WalmartStaffSelect(cog))
            self.add_item(self._threshold_down)
            self.add_item(self._threshold_value)
            self.add_item(self._threshold_up)
            self.add_item(self._extreme_toggle)
        self.add_item(self._dm_toggle)
        self.add_item(self._refresh_button)
        self._sync_control_labels()

    def _sync_control_labels(self) -> None:
        """Button labels/styles follow the current snapshot (or defaults)."""
        threshold = DEFAULT_STARTING_DEAL_PERCENT
        extreme = False
        dm_enabled = False
        if self.state is not None:
            threshold = self.state.threshold_percent
            extreme = self.state.extreme_enabled
            dm_enabled = self.state.dm_enabled
        self._threshold_value.label = f"{threshold}%"
        self._extreme_toggle.label = f"Extreme: {'ON' if extreme else 'OFF'}"
        self._extreme_toggle.style = (
            discord.ButtonStyle.success if extreme else discord.ButtonStyle.secondary
        )
        self._dm_toggle.label = f"DM alerts: {'ON' if dm_enabled else 'OFF'}"
        self._dm_toggle.style = (
            discord.ButtonStyle.success if dm_enabled else discord.ButtonStyle.secondary
        )

    def _db(self):
        bot = getattr(self.cog, "bot", None)
        return getattr(bot, "db", None) if bot is not None else None

    @staticmethod
    async def _defer(interaction: discord.Interaction) -> None:
        """Answer Discord before the slow work starts.

        Every control here reads or writes Turso before re-rendering, and
        prod lock waits of 2.5-7.5s blow past the 3s interaction window
        (Unknown interaction 10062, live 2026-11-17). Deferring first
        buys 15 minutes; ``_rerender`` already edits the deferred
        response via ``edit_original_response``.
        """
        if not interaction.response.is_done():
            await interaction.response.defer()

    async def _refresh_state(self, interaction: discord.Interaction) -> None:
        """Re-read the dashboard snapshot; keep the old one if the read fails."""
        bot = getattr(self.cog, "bot", None)
        if bot is None:
            return
        try:
            fresh = await load_menu_dashboard_state(
                bot, interaction.guild_id, interaction.user.id
            )
        except Exception:
            return
        self.state = fresh

    async def _rerender(
        self, interaction: discord.Interaction, *, notice: str | None = None
    ) -> None:
        """Edit this same message with fresh numbers. Never a new message."""
        await self._refresh_state(interaction)
        self._notice = notice
        self._sync_control_labels()
        embed = build_walmart_menu_embed(
            self.state,
            notice=self._notice,
            can_check=self.can_check,
            manage_guild=self.manage_guild,
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def adjust_threshold(
        self, interaction: discord.Interaction, delta: int
    ) -> None:
        """Step the public threshold, clamped to 10–90, then re-render."""
        if not _has_manage_guild(interaction):
            await self._respond(
                interaction,
                "You need **Manage Server** permission to change the deal threshold.",
            )
            return
        db = self._db()
        if db is None or interaction.guild_id is None:
            await self._respond(
                interaction,
                "Couldn't save the threshold — the bot database isn't available right now.",
            )
            return
        current = (
            self.state.threshold_percent
            if self.state is not None
            else DEFAULT_STARTING_DEAL_PERCENT
        )
        new = max(THRESHOLD_STEP_MIN, min(THRESHOLD_STEP_MAX, current + delta))
        await self._defer(interaction)
        try:
            saved = await set_starting_deal_percent(db, interaction.guild_id, new)
        except Exception as error:
            log.exception("Walmart menu threshold write failed", exc_info=error)
            await self._respond(
                interaction,
                f"Couldn't save the threshold: `{type(error).__name__}`",
            )
            return
        await self._rerender(interaction, notice=f"Threshold is now {saved}%.")

    async def toggle_extreme(self, interaction: discord.Interaction) -> None:
        """Flip the server's extreme-leads flag, then re-render."""
        if not _has_manage_guild(interaction):
            await self._respond(
                interaction,
                "You need **Manage Server** permission to change extreme leads.",
            )
            return
        db = self._db()
        if db is None or interaction.guild_id is None:
            await self._respond(
                interaction,
                "Couldn't save the extreme-leads setting — the bot database isn't available right now.",
            )
            return
        if self.state is not None:
            current = self.state.extreme_enabled
        else:
            await self._defer(interaction)
            try:
                current = await extreme_alerts_enabled(db, interaction.guild_id)
            except Exception as error:
                log.exception("Walmart menu extreme read failed", exc_info=error)
                await self._respond(
                    interaction,
                    f"Couldn't read the extreme-leads setting: `{type(error).__name__}`",
                )
                return
        new = not current
        await self._defer(interaction)
        try:
            await set_extreme_alerts_enabled(db, interaction.guild_id, new)
        except Exception as error:
            log.exception("Walmart menu extreme toggle failed", exc_info=error)
            await self._respond(
                interaction,
                f"Couldn't save the extreme-leads setting: `{type(error).__name__}`",
            )
            return
        await self._rerender(
            interaction, notice=f"Extreme leads are now {'ON' if new else 'OFF'}."
        )

    async def toggle_dm_alerts(self, interaction: discord.Interaction) -> None:
        """Flip only the tapping user's own DM preference, then re-render.

        Same write path as /walmart my-alerts enable/disable: read the
        opener's row, save it back with ``enabled`` flipped. No other
        user's row is ever read or written here.
        """
        db = self._db()
        if db is None:
            await self._respond(
                interaction,
                "Couldn't save your DM alert setting — the bot database isn't available right now.",
            )
            return
        await self._defer(interaction)
        try:
            preference = await get_dm_deal_alert_preference(db, interaction.user.id)
            saved = await save_dm_deal_alert_preference(
                db, replace(preference, enabled=not preference.enabled)
            )
        except Exception as error:
            log.exception("Walmart menu DM toggle failed", exc_info=error)
            await self._respond(
                interaction,
                f"Couldn't save your DM alert setting: `{type(error).__name__}`",
            )
            return
        await self._rerender(
            interaction,
            notice=f"Your DM alerts are now {'ON' if saved.enabled else 'OFF'}.",
        )

    async def refresh_dashboard(self, interaction: discord.Interaction) -> None:
        """Re-read state and re-render. Read-only; anyone may refresh."""
        await self._defer(interaction)
        await self._rerender(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    async def run_function(self, interaction: discord.Interaction, key: str) -> None:
        """Run one menu function from a select or button interaction."""

        label, description, usage = MENU_FUNCTIONS[key]
        if key == "check":
            # Own gate: Deal Finder role OR Manage Server. The slash
            # command enforces the same gate via its app_commands.check.
            if not _can_check_deal(interaction):
                await self._respond(
                    interaction,
                    "You need the 🔍 Deal Finder role or Manage Server to check deals.",
                )
                return
        elif key in _MENU_MANAGE_GUILD and not _has_manage_guild(interaction):
            await self._respond(interaction, "You need **Manage Server** permission to use that Walmart function.")
            return
        if key in _MENU_MODAL:
            modal = WalmartCheckModal(self.cog) if key == "check" else WalmartFindModal(self.cog)
            await interaction.response.send_modal(modal)
            return
        if key in _MENU_GUIDANCE:
            embed = discord.Embed(
                title=f"{label}",
                description=f"{description}\n\nRun it with:\n`{usage}`",
                color=discord.Color.orange(),
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return
        if key == "my-alerts":
            # Opens the personalization menu in place. dm_deals defers
            # itself first, so no defer here — and the action is required.
            await self.cog._delegate(
                interaction,
                "my-alerts",
                action=app_commands.Choice(
                    name="Open personalization menu", value="menu"
                ),
            )
            return
        try:
            await self.cog._delegate(interaction, key)
        except Exception as error:  # Menu calls bypass command error handlers.
            log.exception("Walmart menu function %s failed", key, exc_info=error)
            await self._respond(
                interaction,
                f"That Walmart function hit an error: `{type(error).__name__}`",
            )

    @staticmethod
    async def _respond(interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class WalmartStaffSelect(discord.ui.Select):
    """Staff-only select: the 10 server functions. Only added to the
    view for Manage Server openers, so regular members never see it."""

    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        options = [
            discord.SelectOption(
                label=MENU_FUNCTIONS[key][0],
                description=MENU_FUNCTIONS[key][1],
                value=key,
                emoji=_MENU_EMOJIS.get(key),
            )
            for key in MENU_STAFF_KEYS
        ]
        super().__init__(placeholder="Server setup…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.run_function(interaction, self.values[0])


class WalmartMenuCheckButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="Check a deal", emoji="🔍", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.run_function(interaction, "check")


class WalmartMenuFindButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="Find deals", emoji="🔎", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.run_function(interaction, "find")


class WalmartMenuHuntButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="Hunt deals", emoji="🎯", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.run_function(interaction, "hunt")


class WalmartMenuMyAlertsButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="My alerts", emoji="🔔", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.run_function(interaction, "my-alerts")


class WalmartThresholdDownButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="−5%", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.adjust_threshold(interaction, -THRESHOLD_STEP_PERCENT)


class WalmartThresholdValueButton(discord.ui.Button):
    """Display-only current threshold. Disabled, so it never fires."""

    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(
            label=f"{DEFAULT_STARTING_DEAL_PERCENT}%",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.refresh_dashboard(interaction)


class WalmartThresholdUpButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="+5%", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.adjust_threshold(interaction, THRESHOLD_STEP_PERCENT)


class WalmartExtremeToggleButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="Extreme: OFF", style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.toggle_extreme(interaction)


class WalmartDmToggleButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="DM alerts: OFF", style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.toggle_dm_alerts(interaction)


class WalmartRefreshButton(discord.ui.Button):
    def __init__(self, cog: "WalmartCommandCog") -> None:
        self.cog = cog
        super().__init__(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, WalmartMenuView)
        await view.refresh_dashboard(interaction)


class WalmartCheckModal(discord.ui.Modal, title="Check a Walmart deal"):
    link = discord.ui.TextInput(
        label="Walmart link or item ID",
        placeholder="https://www.walmart.com/ip/.../425031866",
        required=True,
        min_length=1,
        max_length=500,
    )

    def __init__(self, cog: "WalmartCommandCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog._delegate(interaction, "check", link=str(self.link.value).strip())


class WalmartFindModal(discord.ui.Modal, title="Find Walmart deals"):
    search = discord.ui.TextInput(
        label="What are you shopping for?",
        placeholder="oled tv, lego, detergent, ssd",
        required=True,
        min_length=1,
        max_length=100,
    )

    def __init__(self, cog: "WalmartCommandCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        search = str(self.search.value).strip()
        if not search:
            await interaction.response.send_message(
                "Type what you're looking for — for example `oled tv`, `lego`, or `detergent`.",
                ephemeral=True,
            )
            return
        await self.cog._delegate(interaction, "find", search=search)
