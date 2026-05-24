from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.services.walmart_card_renderer import build_walmart_cards, strict_discount_percent


def install_walmart_renderer() -> None:
    """Install the strict Walmart renderer while deal_scanner is being slimmed down.

    This intentionally replaces the old `walmart_accuracy` startup hook name so
    startup reads like what it actually does: install the Walmart card renderer.
    The rendering logic itself lives in `walmart_card_renderer.py` as the single
    source of truth.
    """
    if getattr(deal_scanner, "_sniperplug_walmart_renderer_installed", False):
        return
    deal_scanner.build_walmart_cards = build_walmart_cards
    deal_scanner.discount_percent = strict_discount_percent
    deal_scanner._sniperplug_walmart_renderer_installed = True
