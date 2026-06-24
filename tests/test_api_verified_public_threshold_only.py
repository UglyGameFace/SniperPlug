from types import SimpleNamespace

from sniperplug.services.public_deal_quality import (
    is_public_deal_candidate,
    is_public_scout_candidate,
)
from sniperplug.services.direct_search_rescue import direct_match_score


def card(*, discount=0, price=10.0, score=150, label=""):
    return SimpleNamespace(
        discount=discount,
        current_price=price,
        score=score,
        label=label,
        url="https://www.walmart.com/ip/123",
        embed=None,
    )


def test_walmart_cash_or_score_cannot_bypass_50_percent_threshold():
    c = card(discount=0, score=150, label="Walmart Cash from API: $10")
    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_49_percent_does_not_post_when_threshold_is_50():
    c = card(discount=49, score=150, label="Trusted API markdown")
    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_50_percent_api_markdown_can_post():
    c = card(discount=50, score=80, label="Trusted API markdown")
    assert is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_low_trust_reference_blocks_even_high_discount_number():
    c = card(discount=90, score=150, label="Ignored reference: $99 MSRP low-trust/suspicious")
    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)


def test_public_scout_lane_is_disabled():
    c = card(discount=0, score=150, label="Scout lead Walmart API promo")
    assert not is_public_scout_candidate(c, source_label="autoscan:walmart:scout", min_score=95)


def test_generic_category_search_is_not_exact_product_proof():
    assert direct_match_score(
        "toy clearance",
        "AUTERCO 12.5 Yellow School Bus Toy Car Model for Kids",
        sku="123",
        product_id="123",
    ) < 0.45


def test_specific_product_search_can_still_match_but_does_not_bypass_threshold():
    assert direct_match_score(
        "auterco yellow school bus toy",
        "AUTERCO 12.5 Yellow School Bus Toy Car Model for Kids",
    ) >= 0.45
    c = card(discount=0, score=150, label="Search route match — not deal proof")
    assert not is_public_deal_candidate(c, source_label="autoscan:walmart", min_discount=50)
