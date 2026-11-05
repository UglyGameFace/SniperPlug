"""Single authority for "is this retailer stock status unavailable?".

Walmart's Affiliate API reports out-of-stock items as ``stock: "Not available"``
(other values: "Available", "Limited Supply", "Last few items"). Several
decision points previously kept their own token lists, and the authoritative
exact-queue classifier only matched "out of stock" / "unavailable" / "sold out"
— so Walmart's actual out-of-stock value was classified as a live
``verified_markdown`` and posted to Discord.
"""

from __future__ import annotations

from typing import Any

UNAVAILABLE_STOCK_TOKENS = (
    "not available",
    "unavailable",
    "out of stock",
    "out-of-stock",
    "outofstock",
    "sold out",
    "not in stock",
    "no longer available",
    "discontinued",
)


def is_unavailable_stock(value: Any) -> bool:
    text = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    if not text:
        return False
    return any(token in text for token in UNAVAILABLE_STOCK_TOKENS)
