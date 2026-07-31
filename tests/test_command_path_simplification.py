from pathlib import Path

from sniperplug.cogs.settings_dashboard import command_guide_section
from sniperplug.services.command_catalog import COMMAND_CATALOG


def entry(name):
    return next(item for item in COMMAND_CATALOG if item.name == name)


def test_three_primary_paths_are_explicit():
    assert command_guide_section(entry("/deals")) == "Start here"
    assert command_guide_section(entry("/hunt")) == "Start here"
    assert command_guide_section(entry("/discover")) == "Start here"


def test_advanced_walmart_scan_is_not_presented_as_starting_path():
    assert command_guide_section(entry("/walmart_scan")) == "Advanced / diagnostic"
    assert "Normal users should start with `/deals`" in entry("/walmart_scan").when_to_use


def test_workflow_copy_names_only_three_main_search_paths():
    source = Path("sniperplug/cogs/workflow.py").read_text()
    assert "three main search paths" in source
    assert "Start with /deals, /hunt, or /discover" in source
    assert "Advanced raw controls" in source


def test_specialist_commands_are_separated():
    assert command_guide_section(entry("/walmart_cash")) == "Special searches"
    assert command_guide_section(entry("/hd_stock")) == "Special searches"
