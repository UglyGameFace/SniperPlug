from pathlib import Path

from sniperplug.cogs import deal_scanner
from sniperplug.services import walmart_accuracy


def test_walmart_renderer_logic_is_native_not_installed_by_patch_module():
    assert callable(deal_scanner.build_walmart_cards)
    assert callable(deal_scanner.discount_percent)
    assert hasattr(walmart_accuracy, "validate_card_variant_accuracy")


def test_walmart_renderer_install_compat_module_stays_removed():
    assert not Path("sniperplug/services/walmart_renderer_install.py").exists()
