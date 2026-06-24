from pathlib import Path
import re


COG_SOURCES = {
    str(path): path.read_text(encoding="utf-8")
    for path in Path("sniperplug/cogs").glob("*.py")
}
COGS = "\n".join(COG_SOURCES.values())
CATALOG = Path("sniperplug/services/command_catalog.py").read_text(encoding="utf-8")
HOME_DEPOT = Path("sniperplug/cogs/home_depot_local.py").read_text(encoding="utf-8") if Path("sniperplug/cogs/home_depot_local.py").exists() else ""


def slash_command_names() -> set[str]:
    return set(re.findall(r'@app_commands\.command\(\s*name=["\']([^"\']+)["\']', COGS, flags=re.S))


def catalog_names() -> set[str]:
    return set(re.findall(r'name="([^"]+)"', CATALOG))


def test_only_one_public_channel_setup_command_remains():
    names = slash_command_names()

    assert "setup_sniperplug_here" in names

    assert "public_alerts" not in names
    assert "autoscan_setup" not in names
    assert "setup_sniperplug" not in names
    assert "set_channel" not in names


def test_old_channel_setters_removed_from_command_catalog():
    names = catalog_names()

    assert "/setup_sniperplug_here" in names
    assert "/setup_sniperplug" not in names
    assert "/public_alerts" not in names
    assert "/sniperplug set_channel" not in names


def test_core_deal_commands_are_not_removed():
    names = slash_command_names()
    for command in [
        "autoscan_now",
        "autoscan_health",
        "deal_categories",
        "deals",
        "hunt",
        "discover",
        "walmart_scan",
    ]:
        assert command in names, f"Missing deal command: {command}"


def test_home_depot_and_penny_commands_are_not_removed():
    assert "home depot" in HOME_DEPOT.lower() or "depot" in HOME_DEPOT.lower()
    assert "penny" in HOME_DEPOT.lower()
    assert "hd_stock" in HOME_DEPOT
    assert "hd_penny" in HOME_DEPOT


def test_help_text_points_to_single_setup_flow():
    assert "/setup_sniperplug_here" in CATALOG
    assert "/setup_sniperplug channel:" not in CATALOG
    assert "/public_alerts" not in CATALOG


def test_removed_command_error_handlers_are_not_left_dangling():
    assert "@set_channel.error" not in COGS
    assert "@setup_sniperplug.error" not in COGS
    assert "@public_alerts.error" not in COGS
    assert "@autoscan_setup.error" not in COGS
