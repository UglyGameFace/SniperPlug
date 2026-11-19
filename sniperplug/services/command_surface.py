from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RetiredCommand:
    name: str
    replacement: str
    reason: str


# These names must never re-enter the synced top-level command surface. The
# underlying helpers may remain available to canonical dashboards/buttons.
RETIRED_COMMANDS: tuple[RetiredCommand, ...] = (
    RetiredCommand(
        "walmart_scan",
        "/walmart find",
        "The unified deal finder already owns normal and advanced Walmart ranking.",
    ),
    RetiredCommand(
        "walmart_api_probe",
        "/menu (Server setup & health → Provider scan)",
        "Raw provider proof diagnostics live behind the menu's staff screen.",
    ),
    RetiredCommand(
        "open_box_deals",
        "/walmart hunt",
        "Open-box/restored routes are part of Hunt and the global catalog autoscan.",
    ),
    RetiredCommand(
        "sniperplug_workflow",
        "/staff dashboard view:Commands",
        "The dashboard now contains the one canonical command guide.",
    ),
    RetiredCommand(
        "sniperplug_health",
        "/staff dashboard view:Doctor",
        "Health and post-deploy diagnostics were overlapping views of the same runtime.",
    ),
    RetiredCommand(
        "sniperplug_doctor",
        "/staff dashboard view:Doctor",
        "Post-deploy diagnostics now live inside the dashboard instead of a second command.",
    ),
    RetiredCommand(
        "sniperplug_commands",
        "/staff dashboard view:Commands",
        "The command reference now lives inside the dashboard.",
    ),
    RetiredCommand(
        "retailer_autoscan",
        "/staff channel",
        "Walmart discovery is global; per-server interval and daily scan gates are obsolete.",
    ),
    RetiredCommand(
        "retailer_autoscan_status",
        "/staff health",
        "Global catalog coverage and per-server delivery health are shown together.",
    ),
    RetiredCommand(
        "public_alerts_status",
        "/staff dashboard",
        "Public delivery settings are already part of the canonical dashboard.",
    ),
    RetiredCommand(
        "autoscan_clear_cache",
        "/staff active",
        "Destructive duplicate-memory clearing is no longer a routine user command.",
    ),
    RetiredCommand(
        "active_deal_recheck",
        "/staff active",
        "The Active Deals picker can recheck one exact Walmart item safely.",
    ),
    RetiredCommand(
        "active_deals_recheck",
        "/staff active",
        "The Active Deals page already includes a bounded batch recheck button.",
    ),
    RetiredCommand(
        "active_deals_cleanup",
        "/staff active",
        "Stale cache maintenance runs automatically when Active Deals is loaded.",
    ),
    RetiredCommand(
        "hd_penny_zip",
        "/staff hd-penny",
        "The targeted Home Depot penny command already accepts a ZIP and product query.",
    ),
    # Walmart commands folded into the /walmart group (2026-11-17). Each
    # subcommand delegates to the same handler, so these are renames only.
    RetiredCommand(
        "check_deal",
        "/walmart check",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "deals",
        "/walmart find",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "hunt",
        "/walmart hunt",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "walmart_cash",
        "/walmart find",
        "Retired 2026-11-17: no supported Walmart Cash offer feed exists "
        "(the Affiliate Product API is not a Cash feed), so the command "
        "promised offers it could never return. Use /walmart find instead.",
    ),
    RetiredCommand(
        "discover",
        "/menu → Broad sweep",
        "Broad sweeps are a staff menu function now, not a slash command.",
    ),
    RetiredCommand(
        "autoscan_now",
        "/menu → Scan now",
        "Manual scans run from the Walmart staff menu now.",
    ),
    RetiredCommand(
        "autoscan_health",
        "/staff health",
        "Health lives in the one staff group now.",
    ),
    RetiredCommand(
        "deal_threshold",
        "/staff threshold",
        "Server settings live in the one staff group now.",
    ),
    RetiredCommand(
        "deal_categories",
        "/staff categories",
        "Server settings live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart_extreme_alerts",
        "/staff extreme",
        "Server settings live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart_recovery",
        "/staff recovery",
        "Server tools live in the one staff group now.",
    ),
    RetiredCommand(
        "deal_trace",
        "/menu → Trace an item",
        "Item traces run from the Walmart staff menu now.",
    ),
    RetiredCommand(
        "dm_deals",
        "/walmart my-alerts",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "feedback_learning_status",
        "/menu → Feedback",
        "Feedback learning is a staff menu function now.",
    ),
    RetiredCommand(
        "feedback_learning_reset",
        "/menu → Reset feedback",
        "Feedback resets run from the Walmart staff menu, behind a confirm.",
    ),
    RetiredCommand(
        "active_deals",
        "/staff active",
        "Recent deals live in the one staff group now.",
    ),
    RetiredCommand(
        "active_deal_history",
        "/menu → History",
        "Deal history is a staff menu function now.",
    ),
    RetiredCommand(
        "setup_sniperplug_here",
        "/staff channel",
        "Channel setup lives in the one staff group now.",
    ),
    # Historical/nonexistent catalog entries are listed so documentation tests
    # cannot accidentally advertise them again.
    RetiredCommand(
        "setup_sniperplug_here_status",
        "/staff dashboard",
        "This legacy status alias was never part of the canonical runtime.",
    ),
    # eBay consent subcommands (2026-11-17 audit §3 item 10). Research and
    # review_sample never require stored consent — they only read it to decide
    # whether an on-demand result may carry a tracked affiliate link — and no
    # promotional sender exists to consume an opt-in. Collecting consent for
    # a sender that does not exist is worse than not having the commands.
    # Names with a space are group subcommands ("ebay consent", not top-level).
    RetiredCommand(
        "ebay consent",
        "/ebay status",
        "No eBay promotional sender consumes stored consent; research works without it.",
    ),
    RetiredCommand(
        "ebay server",
        "/ebay status",
        "No eBay promotional sender consumes stored server consent; research works without it.",
    ),
    # Consolidation PR 2 (2026-11-19): the picker keeps 7 top-levels —
    # menu, walmart, wishlist, movies, verizon, ebay, staff. Staff tools
    # moved under /staff; everything else below is menu-only (handlers
    # untouched, reachable from /menu). The three /sniperplug demo
    # commands were deleted outright instead (static fake data).
    RetiredCommand(
        "home_depot_search",
        "/staff hd-search",
        "Home Depot tools live in the one staff group now.",
    ),
    RetiredCommand(
        "home_depot_penny_hunt",
        "/staff hd-penny",
        "Home Depot tools live in the one staff group now.",
    ),
    RetiredCommand(
        "hd_stock",
        "/staff hd-stock",
        "Home Depot tools live in the one staff group now.",
    ),
    RetiredCommand(
        "sniperplug_dashboard",
        "/staff dashboard",
        "The owner dashboard lives in the one staff group now.",
    ),
    RetiredCommand(
        "storage_health",
        "/staff dashboard view:Storage",
        "Storage counts are a page of the staff dashboard now.",
    ),
    RetiredCommand(
        "storage_maintenance_now",
        "/menu (Server setup & health → Storage cleanup)",
        "Storage cleanup runs from the menu's server screen, behind a confirm.",
    ),
    RetiredCommand(
        "local_check",
        "/menu (eBay & Home Depot → Local proof check)",
        "Local proof recording is a menu function now.",
    ),
    RetiredCommand(
        "seed_clearance",
        "/menu (eBay & Home Depot → Save a clearance lead)",
        "Saving clearance leads is a menu function now.",
    ),
    RetiredCommand(
        "clearance_bank",
        "/menu (eBay & Home Depot → Clearance bank)",
        "The clearance bank is a menu function now.",
    ),
    RetiredCommand(
        "target_location",
        "/menu (Server setup & health → Target store)",
        "The server Target store is a set-once menu setting now.",
    ),
    RetiredCommand(
        "target_location_clear",
        "/menu (Server setup & health → Clear Target store)",
        "Clearing the server Target store is a menu function now.",
    ),
    RetiredCommand(
        "target_dm_location",
        "/menu (My deals → My Target store)",
        "Your Target store for DMs is a set-once menu setting now.",
    ),
    RetiredCommand(
        "target_dm_location_clear",
        "/menu (My deals → Clear my Target store)",
        "Clearing your Target store is a menu function now.",
    ),
    RetiredCommand(
        "movies-help",
        "/menu (Movies & Verizon → Movie guide)",
        "The movie guide opens from the menu now; the panel itself is unchanged.",
    ),
    RetiredCommand(
        "movies-panel",
        "/menu (Movies & Verizon → Post movie panel)",
        "Posting the permanent movie panel is a staff menu act now.",
    ),
    RetiredCommand(
        "sniperplug",
        "/staff",
        "The owner group was dissolved: staff tools live under /staff, the rest behind /menu.",
    ),
    RetiredCommand(
        "sniperplug routes",
        "/staff dashboard view:Routes",
        "Alert routing is a page of the staff dashboard now.",
    ),
    RetiredCommand(
        "sniperplug provider_scan",
        "/menu (Server setup & health → Provider scan)",
        "Provider scans run from the menu's server screen now.",
    ),
    RetiredCommand(
        "sniperplug test_alert",
        "/staff test-alert",
        "Test alerts live in the one staff group now.",
    ),
    RetiredCommand(
        "sniperplug snipe_plan",
        "Deleted — no replacement",
        "Static demo with invented priorities; deleted outright 2026-11-19.",
    ),
    RetiredCommand(
        "sniperplug monitor_plan",
        "Deleted — no replacement",
        "Static demo with invented targets; deleted outright 2026-11-19.",
    ),
    RetiredCommand(
        "sniperplug scan_test",
        "Deleted — no replacement",
        "Static demo with invented candidates; deleted outright 2026-11-19.",
    ),
    RetiredCommand(
        "walmart setup",
        "/staff channel",
        "Channel setup lives in the one staff group now.",
    ),
    RetiredCommand(
        "walmart threshold",
        "/staff threshold",
        "Server settings live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart categories",
        "/staff categories",
        "Server settings live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart extreme",
        "/staff extreme",
        "Server settings live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart health",
        "/staff health",
        "Health lives in the one staff group now.",
    ),
    RetiredCommand(
        "walmart active",
        "/staff active",
        "Recent deals live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart recovery",
        "/staff recovery",
        "Server tools live in the one staff group now.",
    ),
    RetiredCommand(
        "walmart discover",
        "/menu → Broad sweep",
        "Broad sweeps are a staff menu function now, and staff-gated there.",
    ),
    RetiredCommand(
        "walmart scan-now",
        "/menu → Scan now",
        "Manual scans run from the Walmart staff menu now.",
    ),
    RetiredCommand(
        "walmart recheck",
        "/menu → Recheck a deal",
        "Rechecks run from the Walmart staff menu now.",
    ),
    RetiredCommand(
        "walmart probe",
        "/menu → API probe",
        "The API probe is a staff menu diagnostic now.",
    ),
    RetiredCommand(
        "walmart trace",
        "/menu → Trace an item",
        "Item traces run from the Walmart staff menu now.",
    ),
    RetiredCommand(
        "walmart history",
        "/menu → History",
        "Deal history is a staff menu function now.",
    ),
    RetiredCommand(
        "walmart feedback",
        "/menu → Feedback",
        "Feedback learning is a staff menu function now.",
    ),
    RetiredCommand(
        "walmart feedback-reset",
        "/menu → Reset feedback",
        "Feedback resets run from the Walmart staff menu, behind a confirm.",
    ),
    RetiredCommand(
        "walmart menu",
        "/menu",
        "Two menus was the core complaint; /menu embeds the Walmart screen with a Back button.",
    ),
    RetiredCommand(
        "movies setup",
        "/menu (Movies & Verizon → Movie alerts setup)",
        "Movie alert setup is a staff menu function now.",
    ),
    RetiredCommand(
        "movies scan",
        "/menu (Movies & Verizon → Scan movie sources)",
        "Movie source scans run from the menu now.",
    ),
    RetiredCommand(
        "movies test-alert",
        "/menu (Movies & Verizon → Movie test alert)",
        "Movie test alerts run from the menu now.",
    ),
    RetiredCommand(
        "movies disable",
        "/menu (Movies & Verizon → Disable movie alerts)",
        "Turning movie alerts off is a staff menu function now.",
    ),
    RetiredCommand(
        "movies sources",
        "/movies status",
        "Sources merged into status: it already reports source health.",
    ),
    RetiredCommand(
        "verizon setup",
        "/menu (Movies & Verizon → Verizon setup)",
        "Verizon setup is a staff menu function now.",
    ),
    RetiredCommand(
        "verizon scan",
        "/menu (Movies & Verizon → Verizon scan)",
        "Pasting reward text is a staff menu function now.",
    ),
    RetiredCommand(
        "verizon test-alert",
        "/menu (Movies & Verizon → Verizon test alert)",
        "Verizon test alerts run from the menu now.",
    ),
    RetiredCommand(
        "verizon add-keyword",
        "/menu (Movies & Verizon → Add Verizon keyword)",
        "Keyword edits run from the menu now.",
    ),
    RetiredCommand(
        "verizon remove-keyword",
        "/menu (Movies & Verizon → Remove Verizon keyword)",
        "Keyword edits run from the menu now.",
    ),
    RetiredCommand(
        "verizon reminders",
        "/menu (Movies & Verizon → Verizon reminders)",
        "Reminder toggles run from the menu now.",
    ),
    RetiredCommand(
        "verizon ntfy-setup",
        "/menu (Movies & Verizon → Phone alerts setup)",
        "Phone alerts are one menu entry now, not three commands.",
    ),
    RetiredCommand(
        "verizon ntfy-status",
        "/menu (Movies & Verizon → Phone alerts status)",
        "Phone alerts are one menu entry now, not three commands.",
    ),
    RetiredCommand(
        "verizon ntfy-disable",
        "/menu (Movies & Verizon → Phone alerts off)",
        "Phone alerts are one menu entry now, not three commands.",
    ),
    RetiredCommand(
        "verizon list-keywords",
        "/verizon keywords",
        "Renamed: 'list-' was a programmer prefix.",
    ),
    RetiredCommand(
        "ebay review_sample",
        "/ebay preview",
        "Renamed: underscore and 'sample' were jargon.",
    ),
)

RETIRED_COMMAND_NAMES = frozenset(item.name for item in RETIRED_COMMANDS)

# Every command advertised by the canonical guide is required. Optional feature
# groups such as Movies and Verizon remain outside this contract, but nothing in
# the official command guide may silently disappear during a future refactor.
REQUIRED_CANONICAL_COMMANDS = frozenset(
    {
        "menu",
        "walmart",
        "wishlist",
        "movies",
        "verizon",
        "ebay",
        "staff",
    }
)


def prune_retired_commands(tree: Any) -> tuple[RetiredCommand, ...]:
    """Remove retired top-level chat-input commands before Discord sync.

    Cogs can keep internal helper methods while the live slash-command tree stays
    small and unambiguous. Returning the removed definitions makes startup logs
    and tests explicit about what changed.
    """

    removed: list[RetiredCommand] = []
    for item in RETIRED_COMMANDS:
        if " " in item.name:
            # Group subcommand, e.g. "ebay consent": remove it from its group.
            group_name, sub_name = item.name.split(" ", 1)
            try:
                group = tree.get_command(group_name)
            except Exception:
                group = None
            remove = getattr(group, "remove_command", None)
            if group is None or remove is None:
                continue
            try:
                subcommand = group.get_command(sub_name)
            except Exception:
                subcommand = None
            if subcommand is None:
                continue
            remove(sub_name)
            removed.append(item)
            continue
        try:
            command = tree.get_command(item.name)
        except Exception:
            command = None
        if command is None:
            continue
        try:
            tree.remove_command(item.name)
        except TypeError:
            # Compatibility with fakes/older discord.py signatures.
            tree.remove_command(item.name, guild=None)
        removed.append(item)
    return tuple(removed)


def command_surface_issues(root_commands: Iterable[Any]) -> tuple[str, ...]:
    """Validate canonical names after all cogs are loaded and pruning is done."""

    top_level = tuple(root_commands or ())
    top_names = [str(getattr(command, "name", "") or "") for command in top_level]
    issues: list[str] = []

    duplicates = sorted({name for name in top_names if name and top_names.count(name) > 1})
    if duplicates:
        issues.append(f"duplicate top-level command names: {', '.join(duplicates)}")

    retired_loaded = sorted(RETIRED_COMMAND_NAMES.intersection(top_names))
    for command in top_level:
        group_name = str(getattr(command, "name", "") or "")
        children = getattr(command, "commands", ()) or ()
        for child in children:
            full_name = f"{group_name} {getattr(child, 'name', '') or ''}".strip()
            if full_name in RETIRED_COMMAND_NAMES and full_name not in retired_loaded:
                retired_loaded.append(full_name)
    retired_loaded = sorted(set(retired_loaded))
    if retired_loaded:
        issues.append(f"retired commands still loaded: {', '.join(retired_loaded)}")

    missing = sorted(REQUIRED_CANONICAL_COMMANDS.difference(top_names))
    if missing:
        issues.append(f"required canonical commands missing: {', '.join(missing)}")

    return tuple(issues)


def command_surface_summary(root_commands: Iterable[Any]) -> str:
    top_level = tuple(root_commands or ())
    names = sorted(str(getattr(command, "name", "") or "") for command in top_level)
    return f"top_level={len(names)} names={','.join(name for name in names if name)}"


def replacement_for(command_name: str) -> str | None:
    key = str(command_name or "").strip().lstrip("/")
    for item in RETIRED_COMMANDS:
        if item.name == key:
            return item.replacement
    return None
