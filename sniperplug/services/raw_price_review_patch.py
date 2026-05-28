from __future__ import annotations


def install_raw_price_review_patch() -> None:
    """Compatibility no-op.

    Raw-price review logic now belongs in the native review/scout pipeline.
    This function intentionally does not monkey-patch anything.
    """
    return None
