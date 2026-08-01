from __future__ import annotations

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.public_quality_diagnostics import public_quality_block_reason


def test_quality_diagnostic_reports_low_trust_reference() -> None:
    card = DealCard(
        embed=discord.Embed(title="Low trust"),
        url="https://www.walmart.com/ip/123",
        label="Low trust",
        score=100,
        discount=70,
    )
    card.current_price = 10.0
    card.api_current_price = 10.0
    card.api_reference_price = 100.0
    card.variant_attributes = {"referencePriceTrusted": "no"}

    reason = public_quality_block_reason(card, min_discount=50)

    assert reason == "reference price is low-trust or explicitly blocked"


def test_quality_diagnostic_reports_below_threshold() -> None:
    card = DealCard(
        embed=discord.Embed(title="Under threshold"),
        url="https://www.walmart.com/ip/123",
        label="Under threshold",
        score=100,
        discount=30,
    )
    card.current_price = 70.0
    card.api_current_price = 70.0
    card.api_reference_price = 100.0
    card.direct_product_url = "https://www.walmart.com/ip/123"
    card.deal_lane = "verified_markdown"
    card.variant_attributes = {"referencePriceTrusted": "yes"}

    reason = public_quality_block_reason(card, min_discount=50)

    assert reason == "verified discount 30% is below 50% threshold"
