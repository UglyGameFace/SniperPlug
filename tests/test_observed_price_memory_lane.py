import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.public_deal_quality import LANE_PRICE_MEMORY_DROP, is_public_deal_candidate
from sniperplug.services.walmart_observed_price_memory import build_observed_price_drop_card, decide_candidate


def test_observed_price_memory_drop_can_pass_public_gate():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Hyper Tough Tire Inflator",
        product_url="https://www.walmart.com/ip/123",
        direct_product_url="https://www.walmart.com/ip/123",
        product_id="123",
        current_price=20.0,
        can_add_to_cart=True,
        stock_status="In stock",
    )
    row = {"current_price": 60.0, "lowest_seen_price": 60.0}
    decision = decide_candidate(candidate, identity_key="walmart:123", row=row, current_price=20.0, buyable=True, min_discount=50)
    card = build_observed_price_drop_card(candidate, decision, min_discount=50)

    assert decision.should_public_post
    assert card.deal_lane == LANE_PRICE_MEMORY_DROP
    assert card.variant_attributes["priceMemoryIdentity"] == "walmart:123"
    assert card.variant_attributes["referencePriceTrusted"] == "yes"
    assert is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)


def test_observed_price_memory_gate_requires_identity_and_trusted_marker():
    card = DealCard(
        embed=discord.Embed(title="Memory candidate"),
        url="https://www.walmart.com/ip/789",
        label="Memory candidate",
        discount=80,
        deal_lane=LANE_PRICE_MEMORY_DROP,
        api_current_price=10.0,
        api_reference_price=50.0,
        api_discount_percent=80,
        direct_product_url="https://www.walmart.com/ip/789",
        variant_attributes={},
    )

    assert not is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)
    card.variant_attributes["priceMemoryIdentity"] = "walmart:789"
    assert not is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)
    card.variant_attributes["referencePriceTrusted"] = "yes"
    assert is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)
