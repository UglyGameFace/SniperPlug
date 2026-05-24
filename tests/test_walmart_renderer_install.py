from sniperplug.cogs import deal_scanner
from sniperplug.services.walmart_card_renderer import build_walmart_cards, strict_discount_percent
from sniperplug.services.walmart_renderer_install import install_walmart_renderer


def test_install_walmart_renderer_sets_renderer_functions():
    previous_build = deal_scanner.build_walmart_cards
    previous_discount = deal_scanner.discount_percent
    previous_flag = getattr(deal_scanner, "_sniperplug_walmart_renderer_installed", None)
    if hasattr(deal_scanner, "_sniperplug_walmart_renderer_installed"):
        delattr(deal_scanner, "_sniperplug_walmart_renderer_installed")
    try:
        install_walmart_renderer()

        assert deal_scanner.build_walmart_cards is build_walmart_cards
        assert deal_scanner.discount_percent is strict_discount_percent
        assert getattr(deal_scanner, "_sniperplug_walmart_renderer_installed") is True
    finally:
        deal_scanner.build_walmart_cards = previous_build
        deal_scanner.discount_percent = previous_discount
        if previous_flag is None and hasattr(deal_scanner, "_sniperplug_walmart_renderer_installed"):
            delattr(deal_scanner, "_sniperplug_walmart_renderer_installed")
        elif previous_flag is not None:
            deal_scanner._sniperplug_walmart_renderer_installed = previous_flag
