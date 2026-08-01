from __future__ import annotations

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.walmart_price_memory import (
    PriceMemoryDecision,
    attach_memory_badge,
    attach_price_memory_public_proof,
)


def verified_card() -> DealCard:
    card = DealCard(
        embed=discord.Embed(title="Verified Walmart markdown"),
        url="https://www.walmart.com/ip/123",
        label="Verified Walmart markdown",
        discount=60,
        deal_lane="verified_markdown",
        api_current_price=20.0,
        api_reference_price=50.0,
        api_discount_percent=60.0,
        api_reference_path="walmart.exact_detail.was_price",
        direct_product_url="https://www.walmart.com/ip/123",
        variant_attributes={
            "referencePriceTrusted": "yes",
            "trustedReferenceSource": "walmart.exact_detail.was_price",
        },
    )
    card.current_price = 20.0
    card.typical_price = 50.0
    return card


def legacy_drop_decision(card: DealCard) -> PriceMemoryDecision:
    return PriceMemoryDecision(
        card=card,
        status="new_low",
        reason="legacy per-guild row appeared lower",
        previous_price=100.0,
        current_price=20.0,
        lowest_seen_price=100.0,
    )


def test_legacy_memory_cannot_replace_verified_api_price_proof():
    card = verified_card()
    decision = legacy_drop_decision(card)

    attach_price_memory_public_proof(card, decision)

    assert card.deal_lane == "verified_markdown"
    assert card.api_reference_price == 50.0
    assert card.api_reference_path == "walmart.exact_detail.was_price"
    assert card.variant_attributes["trustedReferenceSource"] == "walmart.exact_detail.was_price"
    assert "priceMemoryIdentity" not in card.variant_attributes


def test_legacy_memory_badge_is_private_metadata_only():
    card = verified_card()
    decision = legacy_drop_decision(card)

    attach_memory_badge(card, decision)

    assert card.deal_lane == "verified_markdown"
    assert card.api_reference_price == 50.0
    assert card.api_reference_path == "walmart.exact_detail.was_price"
    assert any(field.name == "🧠 Price memory" for field in card.embed.fields)
