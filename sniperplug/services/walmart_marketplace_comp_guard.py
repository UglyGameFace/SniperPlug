from __future__ import annotations

from sniperplug.services.walmart_marketplace_comp import flip_estimate


def install_walmart_marketplace_comp_guard() -> None:
    """No-op compatibility hook.

    Marketplace comp protection is now native: marketplace comps are flip/research
    context only and are not trusted Walmart was/reference proof.
    """

    return None
