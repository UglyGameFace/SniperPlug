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
        "/sniperplug provider_scan",
        "Raw provider proof diagnostics belong under the owner-only SniperPlug group.",
    ),
    RetiredCommand(
        "open_box_deals",
        "/walmart hunt",
        "Open-box/restored routes are part of Hunt and the global catalog autoscan.",
    ),
    RetiredCommand(
        "sniperplug_workflow",
        "/sniperplug_dashboard view:Commands",
        "The dashboard now contains the one canonical command guide.",
    ),
    RetiredCommand(
        "sniperplug_health",
        "/sniperplug_dashboard view:Doctor",
        "Health and post-deploy diagnostics were overlapping views of the same runtime.",
    ),
    RetiredCommand(
        "sniperplug_doctor",
        "/sniperplug_dashboard view:Doctor",
        "Post-deploy diagnostics now live inside the dashboard instead of a second command.",
    ),
    RetiredCommand(
        "sniperplug_commands",
        "/sniperplug_dashboard view:Commands",
        "The command reference now lives inside the dashboard.",
    ),
    RetiredCommand(
        "retailer_autoscan",
        "/walmart setup",
        "Walmart discovery is global; per-server interval and daily scan gates are obsolete.",
    ),
    RetiredCommand(
        "retailer_autoscan_status",
        "/walmart health",
        "Global catalog coverage and per-server delivery health are shown together.",
    ),
    RetiredCommand(
        "public_alerts_status",
        "/sniperplug_dashboard",
        "Public delivery settings are already part of the canonical dashboard.",
    ),
    RetiredCommand(
        "autoscan_clear_cache",
        "/walmart active",
        "Destructive duplicate-memory clearing is no longer a routine user command.",
    ),
    RetiredCommand(
        "active_deal_recheck",
        "/walmart active",
        "The Active Deals picker can recheck one exact Walmart item safely.",
    ),
    RetiredCommand(
        "active_deals_recheck",
        "/walmart active",
        "The Active Deals page already includes a bounded batch recheck button.",
    ),
    RetiredCommand(
        "active_deals_cleanup",
        "/walmart active",
        "Stale cache maintenance runs automatically when Active Deals is loaded.",
    ),
    RetiredCommand(
        "hd_penny_zip",
        "/home_depot_penny_hunt",
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
        "/walmart discover",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "autoscan_now",
        "/walmart scan-now",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "autoscan_health",
        "/walmart health",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "deal_threshold",
        "/walmart threshold",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "deal_categories",
        "/walmart categories",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "walmart_extreme_alerts",
        "/walmart extreme",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "walmart_recovery",
        "/walmart recovery",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "deal_trace",
        "/walmart trace",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "dm_deals",
        "/walmart my-alerts",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "feedback_learning_status",
        "/walmart feedback",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "feedback_learning_reset",
        "/walmart feedback-reset",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "active_deals",
        "/walmart active",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "active_deal_history",
        "/walmart history",
        "One Walmart command now holds every Walmart function.",
    ),
    RetiredCommand(
        "setup_sniperplug_here",
        "/walmart setup",
        "One Walmart command now holds every Walmart function.",
    ),
    # Historical/nonexistent catalog entries are listed so documentation tests
    # cannot accidentally advertise them again.
    RetiredCommand(
        "setup_sniperplug_here_status",
        "/sniperplug_dashboard",
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
)

RETIRED_COMMAND_NAMES = frozenset(item.name for item in RETIRED_COMMANDS)

# Every command advertised by the canonical guide is required. Optional feature
# groups such as Movies and Verizon remain outside this contract, but nothing in
# the official command guide may silently disappear during a future refactor.
REQUIRED_CANONICAL_COMMANDS = frozenset(
    {
        "menu",
        "walmart",
        "home_depot_search",
        "home_depot_penny_hunt",
        "hd_stock",
        "local_check",
        "seed_clearance",
        "clearance_bank",
        "sniperplug_dashboard",
        "sniperplug",
        "ebay",
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
