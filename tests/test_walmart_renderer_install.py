from pathlib import Path

from sniperplug.cogs import deal_scanner


def test_walmart_renderer_logic_is_native_not_installed_by_patch_module():
    # NOTE (C6): the walmart_accuracy hasattr assertion was deleted with
    # sniperplug/services/walmart_accuracy.py (zero runtime importers).
    assert callable(deal_scanner.build_walmart_cards)
    assert callable(deal_scanner.discount_percent)


def test_walmart_renderer_install_compat_module_stays_removed():
    assert not Path("sniperplug/services/walmart_renderer_install.py").exists()
