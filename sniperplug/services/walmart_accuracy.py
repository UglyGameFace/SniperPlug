from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.services.walmart_card_renderer import build_walmart_cards, strict_discount_percent


def install_walmart_accuracy_patches() -> None:
    """Compatibility shim while deal_scanner is being wired directly.

    The strict Walmart renderer now lives in `sniperplug.services.walmart_card_renderer`.
    This function remains so existing startup code keeps working, but all actual
    rendering/discount behavior delegates to the dedicated renderer module.
    """
    if getattr(deal_scanner, "_sniperplug_walmart_accuracy_installed", False):
        return
    deal_scanner.build_walmart_cards = build_walmart_cards
    deal_scanner.discount_percent = strict_discount_percent
    deal_scanner._sniperplug_walmart_accuracy_installed = True
