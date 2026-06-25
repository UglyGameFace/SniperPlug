from types import SimpleNamespace

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
