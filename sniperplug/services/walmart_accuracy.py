from __future__ import annotations

from typing import Any

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

def validate_card_variant_accuracy(card: Any) -> bool:
    """Native card variant sanity check for strict Walmart renderer cards.

    Renderers and tests can call this directly to verify a card did not carry a
    selected-option/variant mismatch warning from the Walmart row/offer pipeline,
    either on the card itself or on its underlying deal/candidate, and that the
    rendered embed does not surface a mismatch marker.
    """
    warning = (
        getattr(card, "option_mismatch_warning", None)
        or getattr(card, "variant_mismatch_warning", None)
        or getattr(card, "variant_warning", None)
    )
    if warning:
        return False

    for related_name in ("deal", "candidate"):
        related = getattr(card, related_name, None)
        if related is not None and getattr(related, "option_mismatch_warning", None):
            return False

    embed = getattr(card, "embed", None)
    if embed is None or not hasattr(embed, "to_dict"):
        return True

    rendered = str(embed.to_dict()).lower()
    blocked_markers = (
        "wrong variant",
        "variant mismatch",
        "option mismatch",
        "selected option mismatch",
    )
    return not any(marker in rendered for marker in blocked_markers)

