from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sniperplug.services.command_catalog import (
    COMMAND_CATALOG,
    validate_command_catalog,
)
from sniperplug.services.command_surface import (
    REQUIRED_CANONICAL_COMMANDS,
    RETIRED_COMMAND_NAMES,
    command_surface_issues,
    prune_retired_commands,
)


BOT_SOURCE = Path("sniperplug/bot.py").read_text(encoding="utf-8")
WORKFLOW_SOURCE = Path("sniperplug/cogs/canonical_workflow.py").read_text(
    encoding="utf-8"
)
PUBLIC_SOURCE = Path("sniperplug/cogs/canonical_public_alerts.py").read_text(
    encoding="utf-8"
)
SETTINGS_SOURCE = Path("sniperplug/cogs/canonical_settings.py").read_text(
    encoding="utf-8"
)


class _FakeTree:
    def __init__(self, names: set[str]):
        self.commands = {
            name: SimpleNamespace(name=name)
            for name in names
        }

    def get_command(self, name: str):
        return self.commands.get(name)

    def remove_command(self, name: str, **_kwargs):
        return self.commands.pop(name, None)

    def get_commands(self):
        return list(self.commands.values())


def test_retired_commands_are_removed_without_touching_canonical_names() -> None:
    names = set(REQUIRED_CANONICAL_COMMANDS) | {
        "walmart_scan",
        "retailer_autoscan",
        "sniperplug_health",
        "active_deals_cleanup",
    }
    tree = _FakeTree(names)

    removed = prune_retired_commands(tree)
    remaining = {command.name for command in tree.get_commands()}

    assert {item.name for item in removed} == {
        "walmart_scan",
        "retailer_autoscan",
        "sniperplug_health",
        "active_deals_cleanup",
    }
    assert REQUIRED_CANONICAL_COMMANDS.issubset(remaining)
    assert not RETIRED_COMMAND_NAMES.intersection(remaining)
    assert command_surface_issues(tree.get_commands()) == ()


def test_surface_validation_detects_missing_and_retired_commands() -> None:
    commands = [SimpleNamespace(name="deals"), SimpleNamespace(name="walmart_scan")]

    issues = command_surface_issues(commands)

    assert any("retired commands still loaded" in issue for issue in issues)
    assert any("required canonical commands missing" in issue for issue in issues)


def test_command_catalog_only_advertises_canonical_commands() -> None:
    assert validate_command_catalog() == []
    advertised = {
        entry.name[1:].split()[0]
        for entry in COMMAND_CATALOG
    }
    assert not RETIRED_COMMAND_NAMES.intersection(advertised)
    assert {
        "deals",
        "hunt",
        "discover",
        "walmart_cash",
        "dm_deals",
        "setup_sniperplug_here",
        "sniperplug_dashboard",
        "autoscan_health",
    }.issubset(advertised)


def test_bot_loads_canonical_cogs_and_rebuilds_guild_command_tree() -> None:
    assert "CanonicalWorkflowCog" in BOT_SOURCE
    assert "CanonicalPublicAlertsCog" in BOT_SOURCE
    assert "CanonicalSettingsCog" in BOT_SOURCE
    assert "prune_retired_commands(self.tree)" in BOT_SOURCE
    assert "self.tree.clear_commands(guild=guild)" in BOT_SOURCE
    assert "self.tree.copy_global_to(guild=guild)" in BOT_SOURCE

    assert "await self.add_cog(WorkflowCog(self))" not in BOT_SOURCE
    assert "await self.add_cog(PublicAlertsCog(self))" not in BOT_SOURCE
    assert "await self.add_cog(SettingsDashboardCog(self))" not in BOT_SOURCE
    assert "await self.add_cog(OpenBoxDealsCog(self))" not in BOT_SOURCE
    assert "await self.add_cog(ActiveDealRecheckCog(self))" not in BOT_SOURCE


def test_setup_explains_global_discovery_instead_of_per_server_intervals() -> None:
    assert 'name="setup_sniperplug_here"' in WORKFLOW_SOURCE
    assert "global" in WORKFLOW_SOURCE.lower()
    assert "interval_hours=0" in WORKFLOW_SOURCE
    assert "daily_limit=0" in WORKFLOW_SOURCE
    assert "walmart_unlimited" not in WORKFLOW_SOURCE
    assert "interval_hours:" not in WORKFLOW_SOURCE
    assert "daily_limit:" not in WORKFLOW_SOURCE


def test_one_dashboard_replaces_old_status_and_help_commands() -> None:
    assert 'name="sniperplug_dashboard"' in SETTINGS_SOURCE
    assert "DASHBOARD_VIEW_CHOICES" in SETTINGS_SOURCE
    assert "Doctor / post-deploy checks" in SETTINGS_SOURCE
    assert "Command guide" in SETTINGS_SOURCE

    assert 'name="sniperplug_health"' not in SETTINGS_SOURCE
    assert 'name="sniperplug_doctor"' not in SETTINGS_SOURCE
    assert 'name="sniperplug_commands"' not in SETTINGS_SOURCE


def test_autoscan_health_reports_global_coverage_and_server_fanout() -> None:
    assert 'name="autoscan_health"' in PUBLIC_SOURCE
    assert "Global catalog coverage" in PUBLIC_SOURCE
    assert "Exact verification queue" in PUBLIC_SOURCE
    assert "Live fanout enrollment" in PUBLIC_SOURCE
    assert 'name="retailer_autoscan"' not in PUBLIC_SOURCE
    assert 'name="retailer_autoscan_status"' not in PUBLIC_SOURCE
    assert 'name="public_alerts_status"' not in PUBLIC_SOURCE
