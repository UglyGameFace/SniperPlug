from __future__ import annotations

"""Source-informed Walmart bargain triage without a closed SKU/model whitelist.

This is *not* proof of a markdown: it only decides which unusually cheap items
merit timely exact-offer verification and opt-in lead delivery. Proof and seller
checks remain at the authoritative exact-detail/posting boundary.
"""

import re
from typing import Any

# Product value must not be inferred from a case or replacement part bearing
# the name of an expensive device. Apply these exclusions before any positive
# Walmart category, product type, or trusted reference signal.
ACCESSORY = re.compile(
    r"\b(?:case|cover|protector|charger|cable|adapter|replacement|parts? only"
    r"|box only|mount|holder|remote control|display dummy|sticker|decal|"
    r"screen protector|refill|repair part|protective sleeve)\b",
    re.IGNORECASE,
)

# Walmart categoryPath is a retailer-origin product taxonomy, not a model-name
# allowlist. That permits new brands and product generations automatically.
VALUABLE_DEPARTMENTS = (
    "electronics", "computers", "tablet", "cell phones", "video games",
    "televisions", "home theater", "appliances", "furniture", "power tools",
    "jewelry", "cameras", "sporting goods", "fitness equipment",
    "smart home", "audio equipment",
)
ACCESSORY_DEPARTMENTS = (
    "accessories", "cases", "chargers", "cables", "replacement parts",
    "screen protectors", "mounts", "components",
)
# General product nouns allow new brands and models without curated SKU names.
DURABLE_PRODUCT_TYPE = re.compile(
    r"\b(?:tablet|smartphone|cell\s*phone|mobile\s*phone|laptop|notebook|"
    r"desktop\s*(?:computer|pc)|computer|television|oled\s*tv|qled\s*tv|"
    r"monitor|gaming\s*console|game\s*console|handheld\s*console|"
    r"graphics\s*card|gpu|camera|projector|drone|robot\s*vacuum|"
    r"refrigerator|dishwasher|washing\s*machine|dryer|power\s*tool|"
    r"electric\s*bike|treadmill|smartwatch|headphones|earbuds|"
    r"coffee\s*maker|printer|office\s*chair|mattress)\b",
    re.IGNORECASE,
)
# Compatibility fallback for earlier exact snapshots lacking source category;
# no fixed model numbers, and new category/type matches do not need this.
DEVICE_FAMILY = re.compile(
    r"\b(?:galaxy\s+(?:tab|s\d+|z\s*(?:fold|flip)|a\d+)|"
    r"iphone|ipad|pixel\s*\d+|macbook|playstation\s*\d+|"
    r"xbox\s+series|nintendo\s+switch)\b",
    re.IGNORECASE,
)


def primary_product_evidence(candidate: Any, *, trusted_reference: float | None = None) -> str | None:
    """Return why a low-priced item may be substantial, or None for weak signals.

    This does not claim an MSRP or independently establish retail value. For
    unrecognized products with no retailer category or trustworthy baseline,
    keep the offer private rather than guessing a public windfall.
    """
    if candidate is None:
        return None
    title = " ".join(
        str(value or "") for value in (
            getattr(candidate, "title", ""),
            getattr(candidate, "parent_title", ""),
        )
    ).strip()
    if not title or ACCESSORY.search(title):
        return None
    attrs = getattr(candidate, "variant_attributes", None) or {}
    category = str(attrs.get("category") or attrs.get("categoryPath") or "").lower()
    if category and any(dept in category for dept in VALUABLE_DEPARTMENTS):
        if not any(bad in category for bad in ACCESSORY_DEPARTMENTS):
            return "walmart_category"
    if DURABLE_PRODUCT_TYPE.search(title):
        return "product_type"
    if DEVICE_FAMILY.search(title):
        return "device_family"
    if trusted_reference is not None:
        try:
            if float(trusted_reference) >= 50:
                return "trusted_reference"
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def is_priority_extreme_discovery(
    candidate: Any, *, current_price: float | None, trusted_reference: float | None
) -> bool:
    """Boost category-independent, potentially valuable <=$10 leads in the shared queue.

    A search result is never treated as a verified offer: this only changes
    ordering inside existing bounded queues; exact detail still must confirm it.
    """
    try:
        if current_price is None or not 0 < float(current_price) <= 10:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return primary_product_evidence(
        candidate, trusted_reference=trusted_reference
    ) is not None
