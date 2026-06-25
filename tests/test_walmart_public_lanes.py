from types import SimpleNamespace

import discord

from sniperplug.cogs.deal_scanner import DealCard, build_walmart_cards
from sniperplug.cogs.open_box_deals import build_open_box_cards
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult

from sniperplug.services.public_deal_quality import (
    LANE_OPEN_BOX_LIKE_NEW,
    LANE_RESTORED_REFURBISHED,
    LANE_VERIFIED_MARKDOWN,
    LANE_WALMART_CASH,
    is_public_deal_candidate,
)


def card(**overrides):
    base = dict(
        label="",
        url="https://www.walmart.com/ip/123",
        direct_product_url="https://www.walmart.com/ip/123",
        embed=None,
        current_price=None,
        discount=0,
        score=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_open_box_22_vs_120_posts_at_82_percent():
    c = card(
        deal_lane=LANE_OPEN_BOX_LIKE_NEW,
        api_current_price=22.00,
        api_reference_price=120.00,
        api_discount_percent=81.67,
        api_condition="Open Box - Like New",
        api_condition_path="condition.type",
        api_price_path="salePrice",
        api_reference_path="wasPrice",
        seller_name="Walmart",
        fulfillment_type="shipping",
        label="Open box card with MSRP text in display is still structured",
    )

    assert is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_open_box_without_reference_price_stays_private():
    c = card(
        deal_lane=LANE_OPEN_BOX_LIKE_NEW,
        api_current_price=22.00,
        api_discount_percent=82,
        api_condition="Open Box - Like New",
        api_condition_path="condition.type",
        api_price_path="salePrice",
    )

    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_msrp_text_alone_does_not_prove_a_public_deal():
    c = card(
        label="MSRP $120 but no Walmart structured current/reference math",
        current_price=22.00,
        discount=82,
    )

    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_msrp_word_does_not_block_structured_verified_deal():
    c = card(
        deal_lane=LANE_VERIFIED_MARKDOWN,
        api_current_price=22.00,
        api_reference_price=120.00,
        api_discount_percent=81.67,
        api_price_path="salePrice",
        api_reference_path="wasPrice",
        label="Display includes MSRP for user context",
    )

    assert is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_walmart_cash_lane_does_not_post_as_public_markdown():
    c = card(
        deal_lane=LANE_WALMART_CASH,
        api_current_price=22.00,
        api_reference_price=120.00,
        api_discount_percent=81.67,
        label="Walmart Cash reward from API",
    )

    assert not is_public_deal_candidate(c, source_label="autoscan:walmart_cash", min_discount=50)


def test_walmart_cash_text_does_not_block_separate_structured_markdown_lane():
    c = card(
        deal_lane=LANE_VERIFIED_MARKDOWN,
        api_current_price=22.00,
        api_reference_price=120.00,
        api_discount_percent=81.67,
        api_price_path="salePrice",
        api_reference_path="wasPrice",
        label="Walmart Cash also visible on page but markdown lane is separate",
    )

    assert is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_public_alert_threshold_still_enforced_for_condition_deals():
    c = card(
        deal_lane=LANE_RESTORED_REFURBISHED,
        api_current_price=89.00,
        api_reference_price=120.00,
        api_discount_percent=25.83,
        api_condition="Restored",
        api_condition_path="condition.type",
        api_price_path="salePrice",
        api_reference_path="wasPrice",
    )

    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_restored_refurbished_posts_only_with_structured_threshold_math():
    c = card(
        deal_lane=LANE_RESTORED_REFURBISHED,
        api_current_price=39.00,
        api_reference_price=120.00,
        api_discount_percent=67.5,
        api_condition="Refurbished",
        api_condition_path="condition.type",
        api_price_path="salePrice",
        api_reference_path="wasPrice",
    )

    assert is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_deal_card_exposes_structured_proof_fields_directly():
    c = DealCard(
        embed=discord.Embed(title="Open box test"),
        url="https://www.walmart.com/ip/123",
        label="native proof card",
        deal_lane=LANE_OPEN_BOX_LIKE_NEW,
        api_current_price=22.00,
        api_reference_price=120.00,
        api_discount_percent=81.67,
        api_condition="Open Box - Like New",
        api_condition_path="condition.type",
        api_price_path="salePrice",
        api_reference_path="wasPrice",
        direct_product_url="https://www.walmart.com/ip/123",
        variant_attributes={"trustedReferenceSource": "wasPrice"},
    )

    assert c.api_current_price == 22.00
    assert c.api_reference_price == 120.00
    assert c.deal_lane == LANE_OPEN_BOX_LIKE_NEW
    assert is_public_deal_candidate(c, source_label="unit", min_discount=50)


def test_build_walmart_cards_populates_native_structured_proof_fields():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Open Box Vacuum",
        product_url="https://www.walmart.com/ip/123",
        current_price=22.00,
        typical_price=120.00,
        deal_lane=LANE_OPEN_BOX_LIKE_NEW,
        api_current_price=22.00,
        api_reference_price=120.00,
        api_discount_percent=81.67,
        api_condition="Open Box - Like New",
        api_condition_path="condition.type",
        api_reference_path="wasPrice",
        api_price_path="salePrice",
        direct_product_url="https://www.walmart.com/ip/123",
        product_id="123",
        sku="123",
        condition="Open Box - Like New",
        variant_attributes={"trustedReferenceSource": "wasPrice", "currentPriceSource": "salePrice"},
    )
    result = ProviderScanResult(provider_key="walmart", candidates=(candidate,))

    cards = build_walmart_cards(result, min_discount=50, alerts_only=False)

    assert len(cards) == 1
    c = cards[0]
    assert c.deal_lane == LANE_OPEN_BOX_LIKE_NEW
    assert c.api_current_price == 22.00
    assert c.api_reference_price == 120.00
    assert c.api_discount_percent == 81.67
    assert c.api_condition == "Open Box - Like New"
    assert c.api_condition_path == "condition.type"
    assert c.api_reference_path == "wasPrice"
    assert c.api_price_path == "salePrice"
    assert c.direct_product_url == "https://www.walmart.com/ip/123"


def test_open_box_builder_uses_native_dealcard_fields():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Restored Vacuum",
        product_url="https://www.walmart.com/ip/456",
        current_price=39.00,
        typical_price=120.00,
        deal_lane=LANE_RESTORED_REFURBISHED,
        api_current_price=39.00,
        api_reference_price=120.00,
        api_discount_percent=67.5,
        api_condition="Refurbished",
        api_condition_path="condition.type",
        api_reference_path="wasPrice",
        api_price_path="salePrice",
        direct_product_url="https://www.walmart.com/ip/456",
        product_id="456",
        sku="456",
        condition="Refurbished",
        variant_attributes={"trustedReferenceSource": "wasPrice", "currentPriceSource": "salePrice"},
    )
    result = ProviderScanResult(provider_key="walmart", candidates=(candidate,))

    cards = build_open_box_cards(result, min_discount=50)

    assert len(cards) == 1
    c = cards[0]
    assert c.deal_lane == LANE_RESTORED_REFURBISHED
    assert c.api_current_price == 39.00
    assert c.api_reference_price == 120.00
    assert c.api_condition == "refurbished"
    assert c.direct_product_url == "https://www.walmart.com/ip/456"
