from __future__ import annotations

from typing import Any

from sniperplug.services import public_deal_quality as quality


def public_quality_block_reason(
    card: Any,
    *,
    source_label: str = "",
    min_discount: int = 50,
) -> str:
    """Return the first concrete reason a card cannot enter the public lane."""

    if quality.is_review_or_watchlist(card, source_label=source_label):
        return "review/scout-only card; not verified markdown"
    if quality.has_low_trust_reference(card, source_label=source_label):
        return "reference price is low-trust or explicitly blocked"
    if quality.display_reference_without_proof(card, source_label=source_label):
        return "displayed MSRP/reference text has no structured proof"
    if not quality.has_real_price(card):
        return "missing a numeric exact current price"
    if not quality.direct_product_url(card):
        return "missing a direct Walmart product URL"

    lane = quality.normalized_lane(card)
    if lane in quality.PRIVATE_PROMO_LANES:
        return f"private promo lane `{lane}` cannot auto-post as markdown"

    discount = quality.structured_discount(card)
    if discount is None:
        return "trusted current/reference prices do not produce a markdown"
    if discount < max(1, int(min_discount)):
        return f"verified discount {discount:.0f}% is below {int(min_discount)}% threshold"

    if lane == quality.LANE_PRICE_MEMORY_DROP:
        if not quality.has_global_exact_offer_memory_proof(card):
            return "observed-price memory fingerprint is incomplete or unstable"
        return "price-memory proof passed but a later public gate blocked it"

    if lane in {
        quality.LANE_OPEN_BOX_LIKE_NEW,
        quality.LANE_RESTORED_REFURBISHED,
    }:
        condition = quality.normalized_condition(
            quality.attr_value(card, "api_condition", "condition")
        )
        if not condition:
            return "condition-specific lane is missing exact condition proof"
        if not quality.has_structured_reference_proof(card):
            return "condition-specific lane is missing structured reference proof"
        if lane == quality.LANE_OPEN_BOX_LIKE_NEW and not quality.is_open_box_condition(
            condition
        ):
            return f"condition `{condition}` does not match open-box lane"
        if lane == quality.LANE_RESTORED_REFURBISHED and not quality.is_restored_condition(
            condition
        ):
            return f"condition `{condition}` does not match restored lane"

    if lane not in quality.PUBLIC_PRICE_LANES | {
        quality.LANE_CLEARANCE,
        quality.LANE_ROLLBACK,
    }:
        return f"unsupported public deal lane `{lane}`"

    return "public quality proof passed; duplicate/freshness gate blocked it"
