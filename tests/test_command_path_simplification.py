from sniperplug.cogs.settings_dashboard import command_guide_section
from sniperplug.services.command_catalog import COMMAND_CATALOG


def entry(name):
    return next(item for item in COMMAND_CATALOG if item.name == name)


def test_primary_deal_paths_are_explicit():
    assert command_guide_section(entry("/deals")) == "Start here"
    assert command_guide_section(entry("/hunt")) == "Start here"
    assert command_guide_section(entry("/discover")) == "Start here"


def test_advanced_duplicate_walmart_scan_is_retired():
    names = {item.name for item in COMMAND_CATALOG}
    assert "/walmart_scan" not in names
    assert "known-product search" in entry("/deals").purpose.lower() or "specific walmart product" in entry("/deals").purpose.lower()


def test_one_dashboard_owns_workflow_health_and_command_help():
    dashboard = entry("/sniperplug_dashboard")
    assert "Overview, Doctor, or Commands" in dashboard.purpose
    assert "/sniperplug_workflow" not in {item.name for item in COMMAND_CATALOG}
    assert "/sniperplug_health" not in {item.name for item in COMMAND_CATALOG}
    assert "/sniperplug_doctor" not in {item.name for item in COMMAND_CATALOG}
    assert "/sniperplug_commands" not in {item.name for item in COMMAND_CATALOG}


def test_specialist_commands_are_separated():
    assert command_guide_section(entry("/walmart_cash")) == "Special searches"
    assert command_guide_section(entry("/hd_stock")) == "Special searches"
    assert command_guide_section(entry("/dm_deals")) == "Helpful shortcuts"
