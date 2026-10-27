from __future__ import annotations

"""Walmart extreme-price leads, kept separate from verified markdown alerts."""

import re
from typing import Any

from sniperplug.services.public_deal_quality import walmart_item_id_from_url
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity

MAX_PRICE_CENTS = 1000
PRIMARY_PRODUCT = re.compile(
    r"\b(?:galaxy\s+(?:s\d{2}|z\s*(?:fold|flip)|a\d{2})|s2\d\s*(?:fe|ultra|plus)?"
    r"|iphone\s*\d{2}|ipad\s*(?:pro|air|mini|\d+)|pixel\s*[6-9]"
    r"|macbook|laptop|playstation\s*[45]|ps[45]|xbox\s+(?:series|one)"
    r"|nintendo\s+switch|oled\s+tv|robot\s+vacuum)\b",
    re.IGNORECASE,
)
ACCESSORY = re.compile(
    r"\b(?:case|cover|protector|charger|cable|adapter|replacement|parts? only"
    r"|box only|mount|holder|remote control|display dummy)\b",
    re.IGNORECASE,
)


def eligible_exact_extreme_candidate(candidate: Any) -> bool:
    """Allow a real exact price without inventing a discount/reference price."""
    if candidate is None or exact_offer_identity(candidate) is None:
        return False
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if str(attrs.get("exactDetailPriceProof", "")).strip().lower() != "yes":
        return False
    item_id = str(attrs.get("exactDetailItemId") or "")
    url = getattr(candidate, "direct_product_url", None) or getattr(candidate, "product_url", "")
    if not item_id.isdigit() or walmart_item_id_from_url(url) != item_id:
        return False
    if str(getattr(candidate, "retailer", "") or "").strip().lower() != "walmart":
        return False
    try:
        current = float(candidate.api_current_price)
        displayed = float(candidate.current_price)
    except (TypeError, ValueError, OverflowError):
        return False
    if not (0 < current <= MAX_PRICE_CENTS / 100 and abs(current - displayed) < 0.01):
        return False
    if candidate.can_add_to_cart is not True:
        return False
    if candidate.is_member_only or candidate.is_checkout_price:
        return False
    stock = str(candidate.stock_status or "").lower()
    if any(x in stock for x in ("sold out", "out of stock", "unavailable")):
        return False
    title = " ".join(str(x or "") for x in (candidate.title, candidate.parent_title))
    return bool(PRIMARY_PRODUCT.search(title) and not ACCESSORY.search(title))
