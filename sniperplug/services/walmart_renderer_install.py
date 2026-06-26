from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.services.walmart_card_renderer import build_walmart_cards, strict_discount_percent


def install_walmart_renderer() -> None:
    """Install native Walmart renderer functions for startup/back-compat tests."""

    if getattr(deal_scanner, "_sniperplug_walmart_renderer_installed", False):
        return None
    deal_scanner.build_walmart_cards = build_walmart_cards
    deal_scanner.discount_percent = strict_discount_percent
    deal_scanner._sniperplug_walmart_renderer_installed = True
    return None
