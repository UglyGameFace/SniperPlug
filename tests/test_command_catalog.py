from sniperplug.services.command_catalog import COMMAND_CATALOG, entries_for_audience, validate_command_catalog


def test_command_catalog_is_valid():
    assert validate_command_catalog() == []


def test_command_catalog_has_core_commands():
    names = {entry.name for entry in COMMAND_CATALOG}

    assert "/setup_sniperplug" in names
    assert "/sniperplug_workflow" in names
    assert "/deals" in names
    assert "/hunt" in names
    assert "/discover" in names
    assert "/public_alerts" in names
    assert "/retailer_autoscan" in names
    assert "/sniperplug_dashboard" in names


def test_entries_for_audience_filters_owner_commands():
    names = {entry.name for entry in entries_for_audience("owner")}

    assert "/setup_sniperplug" in names
    assert "/sniperplug_dashboard" in names
    assert "/public_alerts" in names
    assert "/deals" not in names
