from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.services.walmart_card_renderer import build_walmart_cards, strict_discount_percent


def install_walmart_accuracy_patches() -> None:
    """Install the strict Walmart renderer exactly once for legacy startup paths.

    The permanent renderer lives in `sniperplug.services.walmart_card_renderer`.
    This shim remains for older startup imports, but it is idempotent so the bot
    cannot repeatedly patch the same Walmart card behavior during boot.
    """
    if getattr(deal_scanner, "_sniperplug_walmart_accuracy_installed", False):
        return
    if deal_scanner.build_walmart_cards is not build_walmart_cards:
        deal_scanner.build_walmart_cards = build_walmart_cards
    if deal_scanner.discount_percent is not strict_discount_percent:
        deal_scanner.discount_percent = strict_discount_percent
    deal_scanner._sniperplug_walmart_accuracy_installed = True
