from sniperplug.services.command_catalog import COMMAND_CATALOG, entries_for_audience, validate_command_catalog
from sniperplug.services.command_surface import RETIRED_COMMAND_NAMES


def test_command_catalog_is_valid():
    assert validate_command_catalog() == []


def test_command_catalog_has_canonical_core_commands():
    names = {entry.name for entry in COMMAND_CATALOG}

    for command in (
        "/setup_sniperplug_here",
        "/sniperplug_dashboard",
        "/autoscan_health",
        "/autoscan_now",
        "/deals",
        "/hunt",
        "/discover",
        "/walmart_cash",
        "/dm_deals",
        "/deal_categories",
        "/deal_threshold",
        "/active_deals",
    ):
        assert command in names

    assert not {f"/{name}" for name in RETIRED_COMMAND_NAMES}.intersection(names)


def test_entries_for_audience_filters_owner_commands():
    names = {entry.name for entry in entries_for_audience("owner")}

    assert "/setup_sniperplug_here" in names
    assert "/sniperplug_dashboard" in names
    assert "/autoscan_health" in names
    assert "/deals" not in names
    assert "/dm_deals" not in names


def test_command_catalog_excludes_removed_duplicate_commands():
    names = {entry.name for entry in COMMAND_CATALOG}

    for removed in (
        "/sniperplug_workflow",
        "/sniperplug_health",
        "/sniperplug_doctor",
        "/sniperplug_commands",
        "/retailer_autoscan",
        "/retailer_autoscan_status",
        "/public_alerts_status",
        "/walmart_scan",
        "/open_box_deals",
        "/active_deal_recheck",
        "/active_deals_recheck",
        "/active_deals_cleanup",
    ):
        assert removed not in names
