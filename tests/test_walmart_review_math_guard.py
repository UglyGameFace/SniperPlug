from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_review_candidates import build_review_candidate_cards


CASH_VALUE_KEY = "walmart" + "Cash" + "Savings"
CASH_OFFER_KEY = "walmart" + "Cash" + "Offered"


def test_review_candidate_blocks_absurd_msrp_reference_math():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Del Monte Peas & Carrots, 14.5 oz Can",
        product_url="https://www.walmart.com/ip/1",
        current_price=2.38,
        typical_price=None,
        sku="1",
        variant_attributes={
            "referenceContextPrice": "13999.99",
            "referenceContextSource": "msrp",
            "availableOnline": "yes",
        },
        signals=("rollback",),
    )

    result = build_review_candidate_cards([candidate])
    assert len(result.cards) == 1
    rendered = str(result.cards[0].embed.to_dict())
    assert "Ignored reference" in rendered
    assert "Reference math: **blocked as low-trust/suspicious**" in rendered
    assert "100%" not in rendered


def test_review_candidate_blocks_cash_value_without_offer_flag():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="LEGO Technic 2022 Ford GT Model Kit",
        product_url="https://www.walmart.com/ip/2",
        current_price=95.99,
        typical_price=None,
        sku="2",
        variant_attributes={
            "referenceContextPrice": "119.99",
            "referenceContextSource": "msrp",
            CASH_VALUE_KEY: "5.00",
        },
        signals=("special buy",),
    )

    result = build_review_candidate_cards([candidate])
    assert len(result.cards) == 1
    rendered = str(result.cards[0].embed.to_dict())
    assert "Walmart Cash from API" not in rendered
    assert "$5.00" not in rendered
    assert result.rejected_bad_value_count == 1


def test_review_candidate_allows_cash_value_with_offer_flag():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="LEGO Technic Ford GT Model Kit",
        product_url="https://www.walmart.com/ip/3",
        current_price=95.99,
        typical_price=None,
        sku="3",
        variant_attributes={
            CASH_VALUE_KEY: "5.00",
            CASH_OFFER_KEY: "yes",
        },
        signals=("special buy",),
    )

    result = build_review_candidate_cards([candidate])
    assert len(result.cards) == 1
    rendered = str(result.cards[0].embed.to_dict())
    assert "Walmart Cash from API" in rendered
    assert "$5.00" in rendered
