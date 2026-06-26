from __future__ import annotations

from typing import Any

from sniperplug.cogs import deal_scanner
from sniperplug.services.walmart_card_renderer import build_walmart_cards, strict_discount_percent


def validate_card_variant_accuracy(card: Any) -> bool:
    """Return whether a Walmart card is safe from known variant/option mismatch flags.

    This is intentionally native module behavior, not a startup installer shim.
    Renderers and tests can call it directly to verify that a card did not carry
    a selected-option mismatch warning from the Walmart row/offer pipeline.
    """
    warning = getattr(card, "option_mismatch_warning", None)
    if warning:
        return False

    deal = getattr(card, "deal", None)
    if deal is not None:
        warning = getattr(deal, "option_mismatch_warning", None)
        if warning:
            return False

    candidate = getattr(card, "candidate", None)
    if candidate is not None:
        warning = getattr(candidate, "option_mismatch_warning", None)
        if warning:
            return False

    return True


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
