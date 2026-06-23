from __future__ import annotations


_PATCHED = False


def install_walmart_savings_reference_patch() -> None:
    """Legacy compatibility shim.

    Walmart savings/reference proof now lives natively in
    `sniperplug.providers.walmart`. This function intentionally does nothing so
    older boot paths can still import/call it without monkey-patching provider
    behavior during startup.
    """
    global _PATCHED
    _PATCHED = True
