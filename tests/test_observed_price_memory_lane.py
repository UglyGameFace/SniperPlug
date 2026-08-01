import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.public_deal_quality import LANE_PRICE_MEMORY_DROP, is_public_deal_candidate
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity
from sniperplug.services.walmart_observed_price_memory import (
    ObservedPriceMemoryDecision,
    build_observed_price_drop_card,
)


def exact_candidate() -> SourceCandidate:
    item_id = "123"
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Hyper Tough Tire Inflator",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id="offer-123",
        current_price=20.0,
        api_current_price=20.0,
        seller_name="Walmart",
        condition="New",
        fulfillment_type="shipping",
        can_add_to_cart=True,
        stock_status="In stock",
        variant_attributes={
            "exactDetailPriceProof": "yes",
            "exactDetailItemId": item_id,
            "sellerId": "WALMART",
            "seller": "Walmart",
            "walmartSeller": "yes",
            "color": "black",
            "size": "standard",
        },
    )


def exact_memory_card() -> DealCard:
    candidate = exact_candidate()
    identity = exact_offer_identity(candidate)
    assert identity is not None
    decision = ObservedPriceMemoryDecision(
        candidate=candidate,
        identity_key=identity.identity_key,
        status="new_low",
        previous_price=60.0,
        current_price=20.0,
        lowest_seen_price=60.0,
        stable_reference_price=60.0,
        stable_seen_count=2,
        candidate_seen_count=1,
        drop_percent=66.67,
        drop_dollars=40.0,
        reason="same exact offer is below a confirmed stable price",
    )
    return build_observed_price_drop_card(
        candidate,
        identity,
        decision,
        min_discount=50,
    )


def test_observed_price_memory_drop_can_pass_public_gate():
    card = exact_memory_card()

    assert card.deal_lane == LANE_PRICE_MEMORY_DROP
    assert card.variant_attributes["priceMemoryIdentity"].startswith("walmart-offer:v1:")
    assert card.variant_attributes["referencePriceTrusted"] == "yes"
    assert is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)


def test_legacy_or_partial_price_memory_markers_cannot_pass_public_gate():
    card = DealCard(
        embed=discord.Embed(title="Memory candidate"),
        url="https://www.walmart.com/ip/789",
        label="Memory candidate",
        discount=80,
        deal_lane=LANE_PRICE_MEMORY_DROP,
        api_current_price=10.0,
        api_reference_price=50.0,
        api_discount_percent=80,
        api_reference_path="sniperplug.global_exact_offer_memory.stable_price",
        direct_product_url="https://www.walmart.com/ip/789",
        selected_offer_id="offer-789",
        variant_attributes={
            "priceMemoryIdentity": "walmart:789",
            "referencePriceTrusted": "yes",
            "trustedReferenceSource": "sniperplug.global_exact_offer_memory.stable_price",
        },
    )

    assert not is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)


def test_every_exact_offer_proof_component_is_required():
    required_keys = (
        "priceMemoryItemId",
        "priceMemoryOfferId",
        "priceMemorySellerKey",
        "priceMemoryVariantKey",
        "priceMemoryConditionKey",
        "priceMemoryFulfillmentKey",
        "priceMemoryStableConfirmations",
        "exactDetailPriceProof",
        "exactDetailItemId",
        "trustedReferenceSource",
        "trustedReferencePrice",
    )

    for key in required_keys:
        card = exact_memory_card()
        card.variant_attributes.pop(key, None)
        assert not is_public_deal_candidate(
            card,
            source_label="autoscan:walmart",
            min_discount=50,
        ), key


def test_offer_and_url_must_match_the_memory_fingerprint():
    card = exact_memory_card()
    card.selected_offer_id = "different-offer"
    assert not is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)

    card = exact_memory_card()
    card.direct_product_url = "https://www.walmart.com/ip/999"
    card.url = "https://www.walmart.com/ip/999"
    assert not is_public_deal_candidate(card, source_label="autoscan:walmart", min_discount=50)
