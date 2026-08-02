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
        "/deals",
        "The unified deal finder already owns normal and advanced Walmart ranking.",
    ),
    RetiredCommand(
        "walmart_api_probe",
        "/sniperplug provider_scan",
        "Raw provider proof diagnostics belong under the owner-only SniperPlug group.",
    ),
    RetiredCommand(
        "open_box_deals",
        "/hunt",
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
        "/setup_sniperplug_here",
        "Walmart discovery is global; per-server interval and daily scan gates are obsolete.",
    ),
    RetiredCommand(
        "retailer_autoscan_status",
        "/autoscan_health",
        "Global catalog coverage and per-server delivery health are shown together.",
    ),
    RetiredCommand(
        "public_alerts_status",
        "/sniperplug_dashboard",
        "Public delivery settings are already part of the canonical dashboard.",
    ),
    RetiredCommand(
        "autoscan_clear_cache",
        "/active_deals",
        "Destructive duplicate-memory clearing is no longer a routine user command.",
    ),
    RetiredCommand(
        "active_deal_recheck",
        "/active_deals",
        "The Active Deals picker can recheck one exact Walmart item safely.",
    ),
    RetiredCommand(
        "active_deals_recheck",
        "/active_deals",
        "The Active Deals page already includes a bounded batch recheck button.",
    ),
    RetiredCommand(
        "active_deals_cleanup",
        "/active_deals",
        "Stale cache maintenance runs automatically when Active Deals is loaded.",
    ),
    RetiredCommand(
        "hd_penny_zip",
        "/home_depot_penny_hunt",
        "The targeted Home Depot penny command already accepts a ZIP and product query.",
    ),
    # Historical/nonexistent catalog entries are listed so documentation tests
    # cannot accidentally advertise them again.
    RetiredCommand(
        "setup_sniperplug_here_status",
        "/sniperplug_dashboard",
        "This legacy status alias was never part of the canonical runtime.",
    ),
)

RETIRED_COMMAND_NAMES = frozenset(item.name for item in RETIRED_COMMANDS)

# Commands whose absence means the public command surface is incomplete. This
# is intentionally small: specialist modules may be optional, but these are the
# stable user and owner entry points.
REQUIRED_CANONICAL_COMMANDS = frozenset(
    {
        "deals",
        "hunt",
        "discover",
        "walmart_cash",
        "dm_deals",
        "setup_sniperplug_here",
        "sniperplug_dashboard",
        "autoscan_health",
        "autoscan_now",
        "deal_categories",
        "deal_threshold",
        "active_deals",
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
