"""One menu for the whole bot: ``/menu``.

The /walmart menu proved the pattern (embed + select + quick buttons, every
pick runs the real function), but it only covered Walmart — the other 20
top-level commands were still scattered through the slash-command picker,
and the menu itself was just a list of the same functions. This hub covers
everything: pick a category, pick a function, it runs right there.

Navigation fits Discord's limits (a select holds at most 25 options): the
hub has one select for the five categories, and each category has its own
select for its functions. Walmart reuses the existing WalmartMenuView
unchanged (plus a Back button), so no Walmart behavior changes here.

Every entry runs its function: no-arg functions delegate straight to the
handler that owns the slash command; functions that need typed input open
a modal (same as /walmart check/find); yes/no functions get a confirm
view; channel functions run in the channel the menu was opened in. Admin
functions enforce the same Manage Server gate the slash command has,
because menu interactions bypass command checks.

Delegation follows the /walmart pattern (``command.callback(cog,
interaction, ...)``) and the same trap applies: delegate targets must name
the cog bot.py actually adds (VerifiedDealScannerCog, never
DealScannerCog). ``tests/test_unified_menu.py`` pins that.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.dm_deal_alerts import ACTION_CHOICES
from sniperplug.cogs.walmart_command import (
    WalmartCheckModal,
    WalmartFindModal,
    WalmartMenuView,
    build_walmart_menu_embed,
)
from sniperplug.services.menu_dashboard_state import (
    MenuDashboardState,
    load_menu_dashboard_state,
)

log = logging.getLogger("sniperplug")

GROUP_DESCRIPTION = "One menu for everything SniperPlug can do."

# eBay research/review-sample carry a per-user cooldown on the slash
# command (2 per 60s); the menu bypasses command checks, so it enforces
# the same limit itself instead of letting menu users spam the API.
_COOLDOWN_LIMIT = 2
_COOLDOWN_WINDOW_S = 60.0

# Shown in place of the status strip when the live state read failed.
# The function list still works; the menu never renders a broken embed.
_STATE_UNAVAILABLE_LINE = "Live state unavailable — the function list still works."

# Per-category accent colors (hub keeps the orange brand color).
# Walmart blue is the real brand blue; the rest are distinct, muted
# accents chosen to match the render's palette.
CATEGORY_COLORS: dict[str, int] = {
    "walmart": 0x0071DC,  # Walmart blue
    "my-deals": 0x9B59B6,  # purple
    "stores": 0x1ABC9C,  # teal
    "movies-verizon": 0xE91E63,  # pink
    "server": 0x607D8B,  # slate
}

# Color language (spec): green = ready/healthy, orange = degraded / needs
# attention, red = down / not configured. HP and Target are off by design,
# never red — that wording is used in the summaries below, not a red dot.
_PROVIDER_EMOJI = {
    "READY": "🟢",
    "DISABLED": "🟠",
    "ERROR": "🔴",
    "UNKNOWN": "🟠",
}


def _format_deal_price(deal: dict) -> str:
    price = deal.get("price")
    discount = deal.get("discount")
    parts: list[str] = []
    if price is not None:
        try:
            parts.append(f"${float(price):,.2f}")
        except (TypeError, ValueError):
            parts.append(str(price))
    if discount is not None:
        parts.append(f"{discount}% off")
    return " · ".join(parts) if parts else "active deal"


def _dm_summary(state: MenuDashboardState) -> str:
    if not state.dm_enabled:
        return "DM alerts OFF"
    detail = "DM alerts ON"
    if state.dm_threshold is not None:
        detail += f" (threshold {state.dm_threshold}%"
        if state.dm_daily_cap is not None:
            detail += f" · up to {state.dm_daily_cap}/day"
        else:
            detail += " · unlimited/day"
        detail += ")"
    return detail


def _deals_today_line(state: MenuDashboardState) -> str:
    if state.deals_today is not None:
        return f"Deals today: {state.deals_today}"
    return (
        "Deals today: unavailable — showing active deals instead "
        f"({state.active_deal_count} active)"
    )


def _status_strip(state: MenuDashboardState) -> str:
    """Hub status strip: one line per live value, read from the snapshot."""
    provider_emoji = _PROVIDER_EMOJI.get(state.provider_status, "🟠")
    extreme = (
        f"Extreme lane: ON ({state.extreme_recent} in last 15 min)"
        if state.extreme_enabled
        else "Extreme lane: OFF"
    )
    delivery = (
        "Delivery: ready"
        if state.delivery_ready
        else f"Delivery: not ready — {state.delivery_summary}"
    )
    return "\n".join(
        [
            f"{provider_emoji} Provider {state.provider_status} — {state.provider_message}",
            f"Public threshold: {state.threshold_percent}%",
            extreme,
            _dm_summary(state),
            f"Wishlist: {state.wishlist_count}/{state.wishlist_cap}",
            _deals_today_line(state),
            f"Active deals: {state.active_deal_count}",
            delivery,
        ]
    )


def _category_summary(state: MenuDashboardState, category_key: str) -> str:
    """One live summary line for a category, from the snapshot only.

    No numbers are invented: categories with no dedicated reader in the
    snapshot reuse the shared live values (provider, threshold, deals).
    HP and Target say "off by design", never red.
    """
    if category_key == "walmart":
        delivery = "delivery ready" if state.delivery_ready else "delivery not ready"
        extreme = (
            f"extreme ON ({state.extreme_recent} in last 15 min)"
            if state.extreme_enabled
            else "extreme OFF"
        )
        return (
            f"threshold {state.threshold_percent}% · "
            f"{state.active_deal_count} active deals · {delivery} · {extreme}"
        )
    if category_key == "my-deals":
        return f"wishlist {state.wishlist_count}/{state.wishlist_cap} · {_dm_summary(state)}"
    if category_key == "stores":
        return (
            f"provider {state.provider_status} · threshold {state.threshold_percent}% · "
            f"{state.active_deal_count} active deals · HP and Target: off by design"
        )
    if category_key == "movies-verizon":
        return f"provider {state.provider_status} · threshold {state.threshold_percent}% · {_deals_today_line(state).lower()}"
    # server
    return f"{state.delivery_summary} · HP and Target: off by design"


@dataclass(frozen=True)
class _ChoiceSpec:
    title: str
    prompt: str
    yes_label: str
    no_label: str
    yes_kwargs: dict
    no_kwargs: dict | None = None


@dataclass(frozen=True)
class _ModalField:
    key: str
    label: str
    placeholder: str
    required: bool
    style: discord.TextStyle = discord.TextStyle.short
    max_length: int = 100


@dataclass(frozen=True)
class _Entry:
    key: str
    label: str
    description: str
    usage: str
    category: str
    mode: str  # "run" | "modal" | "choice" | "channel" | "special"
    cog: str
    method: str
    manage_guild: bool = False
    cooldown: bool = False
    choice: _ChoiceSpec | None = None
    channel_param: str | None = None


MENU_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("walmart", "🛒 Walmart", "Check, find, hunt, alerts, and server setup."),
    ("my-deals", "🔔 My deals", "Your wishlist and personal DM alerts."),
    ("stores", "🏪 eBay & Home Depot", "Research, stock checks, and clearance leads."),
    ("movies-verizon", "🎬 Movies & Verizon", "Free movie tickets and Verizon Shine rewards."),
    ("server", "🛠️ Server setup & health", "Dashboard, storage, diagnostics, Target store."),
)

MENU_ENTRIES: tuple[_Entry, ...] = (
    # -- My deals -------------------------------------------------------
    _Entry("wishlist-add", "Add to wishlist", "Get a DM when one exact item becomes a verified deal.",
           "/wishlist add", "my-deals", "modal", "WishlistCog", "add"),
    _Entry("wishlist-list", "My wishlist", "Show your wishlist and whether deal DMs are on.",
           "/wishlist list", "my-deals", "run", "WishlistCog", "list_items"),
    _Entry("wishlist-remove", "Remove from wishlist", "Stop DM alerts about one item.",
           "/wishlist remove", "my-deals", "modal", "WishlistCog", "remove"),
    _Entry("wishlist-clear", "Clear wishlist", "Remove every item from your wishlist.",
           "/wishlist clear", "my-deals", "choice", "WishlistCog", "clear",
           choice=_ChoiceSpec("Clear your whole wishlist?",
                              "This removes every item. You can add items again any time.",
                              "Clear it", "Keep it", {"confirm": True}, {})),
    _Entry("my-alerts", "My alerts", "Open your personal DM alerts menu.",
           "/walmart my-alerts", "my-deals", "special", "DmDealAlertsCog", "dm_deals"),
    _Entry("target-dm-location", "My Target store", "Set the Target store for your personal deal DMs.",
           "/target_dm_location", "my-deals", "modal", "CanonicalWorkflowCog", "target_dm_location"),
    _Entry("target-dm-location-clear", "Clear my Target store", "Stop location-specific Target deal DMs.",
           "/target_dm_location_clear", "my-deals", "run", "CanonicalWorkflowCog", "target_dm_location_clear"),
    # -- eBay & Home Depot ----------------------------------------------
    _Entry("ebay-research", "eBay research", "Compare live eBay listing prices.",
           "/ebay research", "stores", "modal", "EbayResearchCog", "research", cooldown=True),
    _Entry("ebay-review-sample", "eBay preview", "Preview an actual eBay listing and opt-out controls.",
           "/ebay review-sample", "stores", "modal", "EbayResearchCog", "review_sample", cooldown=True),
    _Entry("ebay-status", "eBay status", "Check eBay integration and your opt-in status.",
           "/ebay status", "stores", "run", "EbayResearchCog", "status"),
    _Entry("home-depot-search", "Home Depot search", "Search Home Depot with quota protection.",
           "/home_depot_search", "stores", "modal", "HomeDepotSearchCog", "home_depot_search", manage_guild=True),
    _Entry("home-depot-penny-hunt", "Home Depot penny hunt", "Hunt penny and clearance candidates.",
           "/home_depot_penny_hunt", "stores", "modal", "HomeDepotSearchCog", "home_depot_penny_hunt", manage_guild=True),
    _Entry("hd-stock", "Home Depot stock", "Check one SKU at a nearby store.",
           "/hd_stock", "stores", "modal", "HomeDepotLocalCog", "hd_stock", manage_guild=True),
    _Entry("local-check", "Local proof check", "Record a local-inventory proof \u2014 no live stock confirmation.",
           "/local_check", "stores", "modal", "LocalInventoryCog", "local_check", manage_guild=True),
    _Entry("seed-clearance", "Save a clearance lead", "Save a clearance lead you found to the review bank.",
           "/seed_clearance", "stores", "modal", "ClearanceBankCog", "seed_clearance", manage_guild=True),
    _Entry("clearance-bank", "Clearance bank", "Review saved clearance leads for this server.",
           "/clearance_bank", "stores", "run", "ClearanceBankCog", "clearance_bank", manage_guild=True),
    # -- Movies & Verizon -----------------------------------------------
    _Entry("movies-latest", "Latest movie tickets", "Refresh and show current free-ticket codes.",
           "/movies latest", "movies-verizon", "run", "MovieTicketsCog", "latest"),
    _Entry("movies-status", "Movie alerts status", "Show movie-ticket destination and source health.",
           "/movies status", "movies-verizon", "run", "MovieTicketsCog", "status"),
    _Entry("movies-sources", "Movie sources", "Show which official movie sources are connected.",
           "/movies sources", "movies-verizon", "run", "MovieTicketsCog", "sources"),
    _Entry("movies-setup", "Movie alerts setup", "Enable free movie-ticket alerts in this channel.",
           "/movies setup", "movies-verizon", "channel", "MovieTicketsCog", "setup",
           manage_guild=True, channel_param="alert_channel"),
    _Entry("movies-scan", "Scan movie sources", "Check every movie source now.",
           "/movies scan", "movies-verizon", "run", "MovieTicketsCog", "scan", manage_guild=True),
    _Entry("movies-test-alert", "Movie test alert", "Post a clearly labeled demo movie alert.",
           "/movies test-alert", "movies-verizon", "run", "MovieTicketsCog", "test_alert", manage_guild=True),
    _Entry("movies-disable", "Disable movie alerts", "Turn off automatic movie-ticket alerts for this server.",
           "/movies disable", "movies-verizon", "run", "MovieTicketsCog", "disable", manage_guild=True),
    _Entry("movies-help", "Movie guide", "Open the private free-movie-ticket guide.",
           "/movies-help", "movies-verizon", "run", "MovieCommandGuideCog", "movies_help"),
    _Entry("movies-panel", "Post movie panel", "Post the permanent free-movie-ticket panel here.",
           "/movies-panel", "movies-verizon", "run", "MovieCommandGuideCog", "movies_panel", manage_guild=True),
    _Entry("verizon-digest", "Verizon rewards", "Show recent Verizon Shine rewards.",
           "/verizon digest", "movies-verizon", "run", "VerizonShineCog", "digest"),
    _Entry("verizon-watch", "Verizon watch", "How Verizon alerts work automatically.",
           "/verizon watch", "movies-verizon", "run", "VerizonShineCog", "watch"),
    _Entry("verizon-status", "Verizon status", "Show Verizon Shine setup and source status.",
           "/verizon status", "movies-verizon", "run", "VerizonShineCog", "status"),
    _Entry("verizon-list-keywords", "Verizon keywords", "List Verizon Shine priority keywords.",
           "/verizon list-keywords", "movies-verizon", "run", "VerizonShineCog", "list_keywords"),
    _Entry("verizon-add-keyword", "Add Verizon keyword", "Add a Verizon Shine priority keyword.",
           "/verizon add-keyword", "movies-verizon", "modal", "VerizonShineCog", "add_keyword", manage_guild=True),
    _Entry("verizon-remove-keyword", "Remove Verizon keyword", "Remove a Verizon Shine priority keyword.",
           "/verizon remove-keyword", "movies-verizon", "modal", "VerizonShineCog", "remove_keyword", manage_guild=True),
    _Entry("verizon-reminders", "Verizon reminders", "Turn Verizon countdown reminders on or off.",
           "/verizon reminders", "movies-verizon", "choice", "VerizonShineCog", "reminders", manage_guild=True,
           choice=_ChoiceSpec("Verizon countdown reminders?",
                              "Turn Verizon Shine countdown reminders on or off.",
                              "Turn on", "Turn off", {"enabled": True}, {"enabled": False})),
    _Entry("verizon-setup", "Verizon setup", "Configure Verizon Shine alerts in this channel.",
           "/verizon setup", "movies-verizon", "channel", "VerizonShineCog", "setup",
           manage_guild=True, channel_param="alert_channel"),
    _Entry("verizon-scan", "Verizon scan", "Paste reward text to save or test an alert.",
           "/verizon scan", "movies-verizon", "modal", "VerizonShineCog", "scan", manage_guild=True),
    _Entry("verizon-test-alert", "Verizon test alert", "Post a safe demo Verizon alert.",
           "/verizon test-alert", "movies-verizon", "run", "VerizonShineCog", "test_alert", manage_guild=True),
    _Entry("verizon-ntfy-setup", "Phone alerts setup", "Create a private ntfy topic for phone alerts.",
           "/verizon ntfy-setup", "movies-verizon", "run", "VerizonShineCog", "ntfy_setup", manage_guild=True),
    _Entry("verizon-ntfy-status", "Phone alerts status", "Show this server's ntfy source status.",
           "/verizon ntfy-status", "movies-verizon", "run", "VerizonShineCog", "ntfy_status"),
    _Entry("verizon-ntfy-disable", "Phone alerts off", "Disable this server's ntfy source.",
           "/verizon ntfy-disable", "movies-verizon", "run", "VerizonShineCog", "ntfy_disable", manage_guild=True),
    # -- Server setup & health ------------------------------------------
    _Entry("sniperplug-dashboard", "Dashboard", "Overview, Doctor, and command guide.",
           "/sniperplug_dashboard", "server", "run", "CanonicalSettingsCog", "sniperplug_dashboard", manage_guild=True),
    _Entry("storage-health", "Storage health", "Storage and live delivery counts.",
           "/storage_health", "server", "run", "StorageAdminCog", "storage_health", manage_guild=True),
    _Entry("storage-maintenance-now", "Storage cleanup", "Run storage cleanup now.",
           "/storage_maintenance_now", "server", "choice", "StorageAdminCog", "storage_maintenance_now", manage_guild=True,
           choice=_ChoiceSpec("Run storage cleanup now?",
                              "Removes stale cache and history rows.",
                              "Run cleanup", "Cancel", {"confirm": True}, {})),
    _Entry("sniperplug-routes", "Alert routes", "Show alert channel routing for this server.",
           "/sniperplug routes", "server", "run", "SniperPlugCog", "routes"),
    _Entry("sniperplug-provider-scan", "Provider scan", "Run a provider scan without posting alerts.",
           "/sniperplug provider-scan", "server", "modal", "SniperPlugCog", "provider_scan", manage_guild=True),
    _Entry("sniperplug-snipe-plan", "Snipe plan", "Static preview of scan priorities \u2014 demo only.",
           "/sniperplug snipe-plan", "server", "run", "SniperPlugCog", "snipe_plan", manage_guild=True),
    _Entry("sniperplug-monitor-plan", "Monitor plan", "Static preview of monitor targets \u2014 demo only.",
           "/sniperplug monitor-plan", "server", "run", "SniperPlugCog", "monitor_plan", manage_guild=True),
    _Entry("sniperplug-test-alert", "Test alert", "Post a realistic test deal alert.",
           "/sniperplug test-alert", "server", "run", "SniperPlugCog", "test_alert", manage_guild=True),
    _Entry("sniperplug-scan-test", "Scan test", "Static preview of demo source candidates.",
           "/sniperplug scan-test", "server", "run", "SniperPlugCog", "scan_test", manage_guild=True),
    _Entry("target-location", "Target store", "Choose the Target store for this server's local alerts.",
           "/target_location", "server", "modal", "CanonicalWorkflowCog", "target_location", manage_guild=True),
    _Entry("target-location-clear", "Clear Target store", "Disable local Target alerts for this server.",
           "/target_location_clear", "server", "run", "CanonicalWorkflowCog", "target_location_clear", manage_guild=True),
)

# key -> (cog name, method name). Used by the delegate regression test.
MENU_DELEGATES: dict[str, tuple[str, str]] = {
    entry.key: (entry.cog, entry.method) for entry in MENU_ENTRIES
}

ENTRIES_BY_CATEGORY: dict[str, tuple[_Entry, ...]] = {
    key: tuple(entry for entry in MENU_ENTRIES if entry.category == key)
    for key, _label, _desc in MENU_CATEGORIES
    if key != "walmart"
}

# Modal fields per entry key: (field key, label, placeholder, required, style, max length).
_MODAL_FIELDS: dict[str, tuple[_ModalField, ...]] = {
    "wishlist-add": (
        _ModalField("item", "Item", "iphone 11, dyson airwrap, rtx 5070", True),
    ),
    "wishlist-remove": (
        _ModalField("item", "Item", "iphone 11", True),
    ),
    "ebay-research": (
        _ModalField("query", "What to research", "iphone 11", False),
    ),
    "ebay-review-sample": (
        _ModalField("query", "What to preview", "iphone 11", True),
    ),
    "home-depot-search": (
        _ModalField("query", "What to search", "drill, patio set, paint", True),
        _ModalField("store_id", "Store number (optional)", "1234", False),
        _ModalField("zip_code", "ZIP code (optional)", "06484", False),
    ),
    "home-depot-penny-hunt": (
        _ModalField("query", "What to hunt", "tools, garden, appliances", True),
        _ModalField("store_id", "Store number (optional)", "1234", False),
        _ModalField("zip_code", "ZIP code (optional)", "06484", False),
    ),
    "hd-stock": (
        _ModalField("sku", "SKU / Internet number", "1001234567", True),
        _ModalField("zip_code", "ZIP code", "06484", True),
        _ModalField("store_id", "Store number (optional)", "1234", False),
    ),
    "local-check": (
        _ModalField("retailer", "Retailer", "walmart, target, homedepot", True),
        _ModalField("sku", "SKU (optional)", "", False),
        _ModalField("zip_code", "ZIP code (optional)", "06484", False),
        _ModalField("observed_price", "Observed price (optional)", "49.00", False),
    ),
    "seed-clearance": (
        _ModalField("retailer", "Retailer", "walmart, target, homedepot", True),
        _ModalField("title", "Product", "Dyson V15 vacuum, $49", True),
        _ModalField("observed_price", "Observed price (optional)", "49.00", False),
        _ModalField("notes", "Notes (optional)", "Shelf tag at Ansonia", False),
    ),
    "target-dm-location": (
        _ModalField("zip_code", "ZIP code", "06484", True),
    ),
    "target-location": (
        _ModalField("zip_code", "ZIP code", "06484", True),
    ),
    "verizon-scan": (
        _ModalField("text", "Reward text", "Paste the Verizon Shine / myAccess reward text",
                    True, discord.TextStyle.paragraph, 2000),
    ),
    "verizon-add-keyword": (
        _ModalField("keyword", "Keyword", "free tablet, bonus data", True),
    ),
    "verizon-remove-keyword": (
        _ModalField("keyword", "Keyword", "free tablet", True),
    ),
    "sniperplug-provider-scan": (
        _ModalField("provider_key", "Provider", "walmart, homedepot", True),
        _ModalField("query", "Search (optional)", "ssd, lego", False),
    ),
}

# Modal keys whose observed_price field must parse as a float.
_PRICE_FIELDS = frozenset({"local-check", "seed-clearance"})


def build_unified_menu_embed(state: MenuDashboardState | None = None) -> discord.Embed:
    """The hub: every category, with a count and a one-liner each.

    With a live ``MenuDashboardState`` snapshot the top field is a status
    strip read fresh at open time (provider health, public threshold,
    extreme lane, the opener's DM alerts, wishlist n/20, deals today —
    or active deals, said so, when the deals-today count is unavailable —
    and the active deal count). Without one (or when the read failed) the
    strip is the fallback line and the function list still works.
    """

    embed = discord.Embed(
        title="📋 SniperPlug menu",
        description="Everything SniperPlug can do, in one place. Pick a category, then pick a function — it runs right there.",
        color=discord.Color.orange(),
    )
    if state is not None and state.state_available:
        embed.add_field(name="📊 Live status", value=_status_strip(state), inline=False)
    else:
        embed.add_field(
            name="📊 Live status",
            value=_STATE_UNAVAILABLE_LINE,
            inline=False,
        )
    counts = {
        "walmart": "18 functions",
        **{key: f"{len(ENTRIES_BY_CATEGORY[key])} functions" for key, _l, _d in MENU_CATEGORIES if key != "walmart"},
    }
    for key, label, desc in MENU_CATEGORIES:
        value = desc
        if state is not None and state.state_available:
            value = f"{desc}\n{_category_summary(state, key)}"
        embed.add_field(name=f"{label} ({counts[key]})", value=value, inline=False)
    if state is not None and state.state_available and state.active_deals:
        lines = [
            f"• {deal.get('title', '')} — {_format_deal_price(deal)}"
            for deal in state.active_deals
        ]
        embed.add_field(name="🔥 Latest active deals", value="\n".join(lines), inline=False)
    embed.set_footer(text="Admin functions need Manage Server. The old slash commands still work too.")
    return embed


def build_category_embed(
    category_key: str, state: MenuDashboardState | None = None
) -> discord.Embed:
    """One category: every function with its usage, so nobody has to guess.

    Per-category accent color (hub keeps orange), the category's live
    summary line at the top when a snapshot is available, then the function
    list as today. With no usable snapshot the top line is the fallback
    and the function list is intact.
    """

    label = dict((key, label) for key, label, _d in MENU_CATEGORIES)[category_key]
    entries = ENTRIES_BY_CATEGORY[category_key]
    embed = discord.Embed(
        title=label,
        description="Pick a function — it runs right there. Use ← Back to return to the full menu.",
        color=discord.Color(CATEGORY_COLORS[category_key]),
    )
    if state is not None and state.state_available:
        summary = _category_summary(state, category_key)
    else:
        summary = _STATE_UNAVAILABLE_LINE
    embed.add_field(name="📊 Live status", value=summary, inline=False)
    lines = [f"`{entry.usage}` — {entry.description}" for entry in entries]
    embed.add_field(name="Functions", value="\n".join(lines), inline=False)
    embed.set_footer(text="Admin functions need Manage Server.")
    return embed


def _has_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
    return bool(permissions is not None and getattr(permissions, "manage_guild", False))


# -- views -------------------------------------------------------------------

class UnifiedMenuView(discord.ui.View):
    """Hub view: one select for the category, quick buttons for the top 3."""

    def __init__(self, cog: "UnifiedMenuCog", state: MenuDashboardState | None = None) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.state = state
        self.add_item(UnifiedCategorySelect(cog))
        self.add_item(UnifiedFindButton(cog))
        self.add_item(UnifiedCheckButton(cog))
        self.add_item(UnifiedWishlistButton(cog))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class UnifiedCategorySelect(discord.ui.Select):
    def __init__(self, cog: "UnifiedMenuCog") -> None:
        self.cog = cog
        options = [
            discord.SelectOption(
                label=label,
                description=desc,
                value=key,
            )
            for key, label, desc in MENU_CATEGORIES
        ]
        super().__init__(placeholder="Choose a category…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.open_category(interaction, self.values[0])


class _QuickButton(discord.ui.Button):
    def __init__(self, cog: "UnifiedMenuCog", *, label: str, emoji: str, style: discord.ButtonStyle) -> None:
        self.cog = cog
        super().__init__(label=label, emoji=emoji, style=style, row=1)


class UnifiedFindButton(_QuickButton):
    def __init__(self, cog: "UnifiedMenuCog") -> None:
        super().__init__(cog, label="Find deals", emoji="🔎", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        walmart_cog = self.cog.bot.get_cog("WalmartCommandCog")
        if walmart_cog is None:
            await self.cog._send_error(interaction, "That Walmart function isn't loaded right now. Try again after the next restart.")
            return
        await interaction.response.send_modal(WalmartFindModal(walmart_cog))


class UnifiedCheckButton(_QuickButton):
    def __init__(self, cog: "UnifiedMenuCog") -> None:
        super().__init__(cog, label="Check a deal", emoji="🔍", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _has_manage_guild(interaction):
            await self.cog._send_error(interaction, "You need **Manage Server** permission to run direct deal checks. Use Find deals for the simple search.")
            return
        walmart_cog = self.cog.bot.get_cog("WalmartCommandCog")
        if walmart_cog is None:
            await self.cog._send_error(interaction, "That Walmart function isn't loaded right now. Try again after the next restart.")
            return
        await interaction.response.send_modal(WalmartCheckModal(walmart_cog))


class UnifiedWishlistButton(_QuickButton):
    def __init__(self, cog: "UnifiedMenuCog") -> None:
        super().__init__(cog, label="Add to wishlist", emoji="⭐", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_MenuModal(self.cog, "wishlist-add"))


class UnifiedCategoryView(discord.ui.View):
    """One category: a select for its functions, Refresh, plus a Back button."""

    def __init__(
        self,
        cog: "UnifiedMenuCog",
        category_key: str,
        state: MenuDashboardState | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.category_key = category_key
        self.state = state
        self.add_item(UnifiedFunctionSelect(cog, category_key))
        self.add_item(UnifiedRefreshButton(cog, category_key))
        self.add_item(UnifiedBackButton(cog))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class UnifiedRefreshButton(discord.ui.Button):
    """Re-read the dashboard state and re-render this category in place.

    Read-only: anyone may refresh, no permission gate, no writes.
    """

    def __init__(self, cog: "UnifiedMenuCog", category_key: str) -> None:
        self.cog = cog
        self.category_key = category_key
        super().__init__(label="🔄 Refresh", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        state = await self.cog._load_state(interaction)
        await self.cog._edit_dashboard(
            interaction,
            embed=build_category_embed(self.category_key, state),
            view=UnifiedCategoryView(self.cog, self.category_key, state),
        )


class UnifiedFunctionSelect(discord.ui.Select):
    def __init__(self, cog: "UnifiedMenuCog", category_key: str) -> None:
        self.cog = cog
        entries = ENTRIES_BY_CATEGORY[category_key]
        options = [
            discord.SelectOption(
                label=entry.label,
                description=entry.description,
                value=entry.key,
            )
            for entry in entries
        ]
        super().__init__(placeholder="Choose a function…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.run_entry(interaction, self.values[0])


class UnifiedBackButton(discord.ui.Button):
    def __init__(self, cog: "UnifiedMenuCog") -> None:
        self.cog = cog
        super().__init__(label="← Back to menu", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        state = await self.cog._load_state(interaction)
        await self.cog._edit_dashboard(
            interaction,
            embed=build_unified_menu_embed(state),
            view=UnifiedMenuView(self.cog, state),
        )


def _build_walmart_embed(state: MenuDashboardState) -> discord.Embed:
    """Walmart embed, passing the snapshot when that builder accepts one.

    The Walmart dashboard is another chunk's file and may still be the
    old no-arg builder in this worktree; fall back to it rather than
    breaking the hub's Walmart category.
    """
    try:
        return build_walmart_menu_embed(state)  # type: ignore[arg-type]
    except TypeError:
        return build_walmart_menu_embed()


class _WalmartMenuViewWithBack(WalmartMenuView):
    """The existing Walmart menu, with a way back to the unified hub."""

    def __init__(
        self,
        walmart_cog,
        menu_cog: "UnifiedMenuCog",
        state: MenuDashboardState | None = None,
    ) -> None:
        try:
            super().__init__(walmart_cog)
        except TypeError:
            # Walmart view may take the snapshot in the other chunk.
            super().__init__(walmart_cog, state)  # type: ignore[call-arg]
        self.menu_cog = menu_cog
        self.add_item(_WalmartBackButton(menu_cog))


class _WalmartBackButton(discord.ui.Button):
    def __init__(self, menu_cog: "UnifiedMenuCog") -> None:
        self.menu_cog = menu_cog
        super().__init__(label="← Back to menu", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        state = await self.menu_cog._load_state(interaction)
        await self.menu_cog._edit_dashboard(
            interaction,
            embed=build_unified_menu_embed(state),
            view=UnifiedMenuView(self.menu_cog, state),
        )


class _ConfirmView(discord.ui.View):
    """Yes/no for one entry: pick, delegate, then disable the buttons."""

    def __init__(self, cog: "UnifiedMenuCog", entry: _Entry) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.entry = entry
        self.consumed = False
        assert entry.choice is not None
        self.add_item(_ConfirmButton(cog, entry, True))
        self.add_item(_ConfirmButton(cog, entry, False))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class _ConfirmButton(discord.ui.Button):
    def __init__(self, cog: "UnifiedMenuCog", entry: _Entry, yes: bool) -> None:
        self.cog = cog
        self.entry = entry
        self.yes = yes
        assert entry.choice is not None
        super().__init__(
            label=entry.choice.yes_label if yes else entry.choice.no_label,
            style=discord.ButtonStyle.primary if yes else discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert isinstance(view, _ConfirmView)
        assert self.entry.choice is not None
        if view.consumed:
            await self.cog._send_error(interaction, "Already handled — pick another function from the menu.")
            return
        view.consumed = True
        for item in view.children:
            item.disabled = True
        if not self.yes:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title=self.entry.choice.title,
                    description="Kept it — nothing changed.",
                    color=discord.Color.orange(),
                ),
                view=view,
            )
            return
        # Delegate first: every handler starts with response.defer/send, so
        # the response must still be fresh. Disable the buttons after, best
        # effort — the menu message is ephemeral, so its edit may fail.
        await self.cog._delegate(interaction, self.entry.key, **self.entry.choice.yes_kwargs)
        message = interaction.message
        if message is not None:
            try:
                await message.edit(view=view)
            except Exception:
                pass


class _MenuModal(discord.ui.Modal):
    """One modal for every typed-input entry: fields from _MODAL_FIELDS."""

    def __init__(self, menu_cog: "UnifiedMenuCog", key: str) -> None:
        entry = next(item for item in MENU_ENTRIES if item.key == key)
        super().__init__(title=entry.label[:45])
        self.menu_cog = menu_cog
        self.key = key
        self._fields = _MODAL_FIELDS[key]
        for field in self._fields:
            self.add_item(
                discord.ui.TextInput(
                    label=field.label,
                    placeholder=field.placeholder,
                    required=field.required,
                    style=field.style,
                    max_length=field.max_length,
                )
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values: dict[str, str | None] = {}
        for field, child in zip(self._fields, self.children):
            assert isinstance(child, discord.ui.TextInput)
            text = (child.value or "").strip()
            values[field.key] = text if text else None
        await self.menu_cog._submit_modal(interaction, self.key, values)


# -- cog ---------------------------------------------------------------------

class UnifiedMenuCog(commands.Cog):
    """``/menu``: one menu for the whole bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # key -> user id -> call timestamps (eBay 2-per-60s menu cooldown).
        self._cooldowns: dict[str, dict[int, list[float]]] = {}

    # -- delegation ----------------------------------------------------

    async def _delegate(
        self,
        interaction: discord.Interaction,
        key: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Run the handler that owns the slash command for this menu entry."""

        cog_name, method_name = MENU_DELEGATES[key]
        cog = self.bot.get_cog(cog_name)
        if cog is None:
            await self._send_error(
                interaction,
                "That function isn't loaded right now. Try again after the next restart.",
            )
            return
        command = getattr(cog, method_name, None)
        if command is None:
            await self._send_error(
                interaction,
                "That function isn't loaded right now. Try again after the next restart.",
            )
            return
        callback = getattr(command, "callback", None)
        try:
            if callback is not None:
                await callback(cog, interaction, *args, **kwargs)
            else:
                await command(interaction, *args, **kwargs)
        except Exception as error:  # Menu calls bypass command error handlers.
            log.exception("Unified menu function %s failed", key, exc_info=error)
            await self._send_error(
                interaction,
                f"That function hit an error: `{type(error).__name__}`",
            )

    @staticmethod
    async def _send_error(interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @staticmethod
    async def _edit_dashboard(
        interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View
    ) -> None:
        """Edit the dashboard message, whether or not we already deferred.

        After ``response.defer()`` the first response must go through
        ``edit_original_response``; before it, ``edit_message``. Every
        dashboard path defers first (Unknown interaction 10062, live
        2026-11-17), so this is the deferred branch in practice.
        """
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)

    # -- dashboard state -------------------------------------------------

    async def _load_state(self, interaction: discord.Interaction) -> MenuDashboardState:
        """Fresh dashboard snapshot for this opener, never raising.

        A failed read returns an unavailable snapshot; the builders render
        the fallback line instead of crashing.
        """
        try:
            guild = getattr(interaction, "guild", None)
            guild_id = getattr(interaction, "guild_id", None) or getattr(guild, "id", None)
            user_id = getattr(getattr(interaction, "user", None), "id", None)
            if guild_id is None or user_id is None:
                return MenuDashboardState()
            return await load_menu_dashboard_state(self.bot, int(guild_id), int(user_id))
        except Exception:
            return MenuDashboardState()

    # -- navigation ------------------------------------------------------

    @app_commands.command(name="menu", description="Open the SniperPlug menu: every function in one place.")
    async def menu(self, interaction: discord.Interaction) -> None:
        # Defer FIRST: the dashboard snapshot reads Turso, and prod lock
        # waits of 2.5-7.5s blow past Discord's 3s interaction window
        # (Unknown interaction 10062, live 2026-11-17). The snapshot
        # loads after the defer, then the menu arrives as a follow-up.
        await interaction.response.defer(ephemeral=True)
        state = await self._load_state(interaction)
        await interaction.followup.send(
            embed=build_unified_menu_embed(state),
            view=UnifiedMenuView(self, state),
            ephemeral=True,
        )

    async def open_category(self, interaction: discord.Interaction, category_key: str) -> None:
        """Swap the hub message for one category's view."""

        await interaction.response.defer()
        state = await self._load_state(interaction)
        if category_key == "walmart":
            walmart_cog = self.bot.get_cog("WalmartCommandCog")
            if walmart_cog is None:
                await self._send_error(
                    interaction,
                    "That Walmart function isn't loaded right now. Try again after the next restart.",
                )
                return
            await self._edit_dashboard(
                interaction,
                embed=_build_walmart_embed(state),
                view=_WalmartMenuViewWithBack(walmart_cog, self, state),
            )
            return
        await self._edit_dashboard(
            interaction,
            embed=build_category_embed(category_key, state),
            view=UnifiedCategoryView(self, category_key, state),
        )

    # -- running entries ---------------------------------------------------

    async def run_entry(self, interaction: discord.Interaction, key: str) -> None:
        """Run one menu entry: in place, by modal, by confirm, or by channel."""

        entry = next(item for item in MENU_ENTRIES if item.key == key)
        if entry.manage_guild and not _has_manage_guild(interaction):
            await self._send_error(
                interaction,
                "You need **Manage Server** permission to use that function.",
            )
            return
        if entry.mode == "modal":
            await interaction.response.send_modal(_MenuModal(self, key))
            return
        if entry.mode == "choice":
            assert entry.choice is not None
            embed = discord.Embed(
                title=entry.choice.title,
                description=entry.choice.prompt,
                color=discord.Color.orange(),
            )
            await interaction.response.edit_message(embed=embed, view=_ConfirmView(self, entry))
            return
        if entry.mode == "channel":
            channel = interaction.channel
            if interaction.guild is None or not isinstance(channel, discord.TextChannel):
                await self._send_error(
                    interaction,
                    "Use this one inside a server text channel, so the bot knows where to post.",
                )
                return
            assert entry.channel_param is not None
            await self._delegate(interaction, key, **{entry.channel_param: channel})
            return
        if entry.mode == "special" and key == "my-alerts":
            # dm_deals opens its personalization menu for action "menu".
            action = next(choice for choice in ACTION_CHOICES if choice.value == "menu")
            await self._delegate(interaction, key, action=action)
            return
        await self._delegate(interaction, key)

    async def _submit_modal(
        self,
        interaction: discord.Interaction,
        key: str,
        values: dict[str, str | None],
    ) -> None:
        """Route a filled modal to its handler, with the same gates as run_entry."""

        entry = next(item for item in MENU_ENTRIES if item.key == key)
        if entry.manage_guild and not _has_manage_guild(interaction):
            await self._send_error(
                interaction,
                "You need **Manage Server** permission to use that function.",
            )
            return
        if entry.cooldown:
            allowed, retry_after = self._check_cooldown(key, interaction.user.id)
            if not allowed:
                await self._send_error(
                    interaction,
                    f"Slow down — try again in {int(retry_after or 0)}s.",
                )
                return
        kwargs: dict[str, object] = {}
        if key in _PRICE_FIELDS:
            price_text = values.get("observed_price")
            if price_text:
                try:
                    kwargs["observed_price"] = float(price_text)
                except ValueError:
                    await self._send_error(
                        interaction,
                        f"That price doesn't look like a number: `{price_text}`. Try 49.00.",
                    )
                    return
        for field in _MODAL_FIELDS[key]:
            if field.key == "observed_price":
                continue
            kwargs[field.key] = values.get(field.key)
        await self._delegate(interaction, key, **kwargs)

    def _check_cooldown(self, key: str, user_id: int) -> tuple[bool, float | None]:
        """2 calls per 60s per user, matching the eBay slash-command cooldown."""

        now = time.monotonic()
        calls = self._cooldowns.setdefault(key, {}).setdefault(user_id, [])
        calls[:] = [stamp for stamp in calls if now - stamp < _COOLDOWN_WINDOW_S]
        if len(calls) >= _COOLDOWN_LIMIT:
            return False, _COOLDOWN_WINDOW_S - (now - calls[0])
        calls.append(now)
        return True, None
