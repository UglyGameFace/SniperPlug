from sniperplug.services.command_catalog import COMMAND_CATALOG, entries_for_audience, validate_command_catalog
from sniperplug.services.command_surface import RETIRED_COMMAND_NAMES


def test_command_catalog_is_valid():
    assert validate_command_catalog() == []


def test_command_catalog_has_canonical_core_commands():
    names = {entry.name for entry in COMMAND_CATALOG}

    for command in (
        "/walmart setup",
        "/sniperplug_dashboard",
        "/walmart health",
        "/autoscan_now",
        "/walmart find",
        "/walmart hunt",
        "/walmart discover",
        "/walmart cash",
        "/walmart my-alerts",
        "/walmart categories",
        "/walmart threshold",
        "/walmart active",
    ):
        assert command in names

    assert not {f"/{name}" for name in RETIRED_COMMAND_NAMES}.intersection(names)


def test_entries_for_audience_filters_owner_commands():
    names = {entry.name for entry in entries_for_audience("owner")}

    assert "/walmart setup" in names
    assert "/sniperplug_dashboard" in names
    assert "/walmart health" in names
    assert "/walmart find" not in names
    assert "/walmart my-alerts" not in names


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
        # The 17 Walmart commands folded into the /walmart group.
        "/check_deal",
        "/deals",
        "/hunt",
        "/walmart_cash",
        "/discover",
        "/autoscan_health",
        "/deal_threshold",
        "/deal_categories",
        "/walmart_extreme_alerts",
        "/walmart_recovery",
        "/deal_trace",
        "/dm_deals",
        "/feedback_learning_status",
        "/feedback_learning_reset",
        "/active_deals",
        "/active_deal_history",
        "/setup_sniperplug_here",
    ):
        assert removed not in names
