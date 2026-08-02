from pathlib import Path
import re


BOT = Path("sniperplug/bot.py").read_text(encoding="utf-8")
CANONICAL_SOURCES = "\n".join(
    Path(path).read_text(encoding="utf-8")
    for path in (
        "sniperplug/cogs/canonical_workflow.py",
        "sniperplug/cogs/canonical_public_alerts.py",
        "sniperplug/cogs/canonical_settings.py",
        "sniperplug/cogs/verified_deal_scanner.py",
        "sniperplug/cogs/auto_discovery.py",
        "sniperplug/cogs/dm_deal_alerts.py",
    )
)
CATALOG = Path("sniperplug/services/command_catalog.py").read_text(encoding="utf-8")
SURFACE = Path("sniperplug/services/command_surface.py").read_text(encoding="utf-8")
HOME_DEPOT_SEARCH = Path("sniperplug/cogs/home_depot_search.py").read_text(encoding="utf-8")
HOME_DEPOT_LOCAL = Path("sniperplug/cogs/home_depot_local.py").read_text(encoding="utf-8")


def slash_command_names(source: str = CANONICAL_SOURCES) -> set[str]:
    return set(re.findall(r'@app_commands\.command\(\s*name=["\']([^"\']+)["\']', source, flags=re.S))


def catalog_names() -> set[str]:
    return set(re.findall(r'name="([^"]+)"', CATALOG))


def test_only_one_public_channel_setup_command_is_loaded():
    names = slash_command_names()
    assert "setup_sniperplug_here" in names
    assert BOT.count("await self.add_cog(CanonicalWorkflowCog(self))") == 1
    assert "await self.add_cog(WorkflowCog(self))" not in BOT


def test_old_channel_and_status_aliases_are_retired():
    names = catalog_names()
    assert "/setup_sniperplug_here" in names
    for retired in (
        "/setup_sniperplug",
        "/public_alerts",
        "/public_alerts_status",
        "/setup_sniperplug_here_status",
        "/retailer_autoscan",
        "/retailer_autoscan_status",
    ):
        assert retired not in names
    assert "public_alerts_status" in SURFACE
    assert "retailer_autoscan" in SURFACE


def test_core_deal_commands_remain_canonical():
    names = slash_command_names()
    for command in (
        "autoscan_health",
        "deal_categories",
        "discover",
        "dm_deals",
        "setup_sniperplug_here",
        "sniperplug_dashboard",
    ):
        assert command in names
    assert "walmart_scan" not in names


def test_home_depot_surface_keeps_targeted_commands_only():
    combined = HOME_DEPOT_SEARCH + HOME_DEPOT_LOCAL
    assert 'name="home_depot_search"' in combined
    assert 'name="home_depot_penny_hunt"' in combined
    assert 'name="hd_stock"' in combined
    assert "/hd_penny_zip" not in CATALOG
    assert '"hd_penny_zip"' in SURFACE


def test_help_text_points_to_dashboard_and_one_setup_flow():
    names = catalog_names()
    assert "/setup_sniperplug_here" in names
    assert "/sniperplug_dashboard" in names
    assert "/autoscan_health" in names
    assert "/public_alerts_status" not in names
    assert "/sniperplug_workflow" not in names


def test_guild_sync_rebuilds_tree_so_stale_discord_commands_are_deleted():
    assert "self.tree.clear_commands(guild=guild)" in BOT
    assert "self.tree.copy_global_to(guild=guild)" in BOT
    assert "prune_retired_commands(self.tree)" in BOT
