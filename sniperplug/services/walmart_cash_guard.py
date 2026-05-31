from __future__ import annotations

from typing import Any

from sniperplug.services.walmart_cash import (
    strict_walmart_promotion_proof as _strict_walmart_promotion_proof,
    walmart_cash_amount,
    walmart_cash_amount_is_sane,
)


_PATCHED = False


def install_strict_walmart_cash_guard() -> None:
    """Temporary compatibility installer.

    The Walmart Cash extraction/sanity logic now lives in
    `sniperplug.services.walmart_cash`. This wrapper exists only until
    `sniperplug.providers.walmart._walmart_promotion_proof` is wired to call the
    helper directly and the startup hook can be safely removed.
    """
    global _PATCHED
    if _PATCHED:
        return

    from sniperplug.providers import walmart as walmart_provider

    walmart_provider._walmart_promotion_proof = strict_walmart_promotion_proof
    _PATCHED = True


def strict_walmart_promotion_proof(item: dict[str, Any]) -> dict[str, str]:
    """Return sanitized coupon/Walmart Cash promo attributes for a Walmart API item."""
    from sniperplug.providers import walmart as walmart_provider

    current_price, _ = walmart_provider._trusted_current_price(item)
    coupon = walmart_provider._promotion_amount(
        item,
        include_terms=("coupon",),
        exclude_terms=("cash", "reward", "walmart cash"),
    )
    return _strict_walmart_promotion_proof(item, current_price=current_price, coupon_amount=coupon)
