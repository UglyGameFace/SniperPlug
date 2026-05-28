from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.raw_price_review_patch import build_review_candidate_cards_with_raw_leads, raw_price_signal


def test_raw_price_signal_allows_fragrance_route_without_reference_price():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz",
        product_url="https://www.walmart.com/ip/1",
        current_price=65.04,
        variant_attributes={"finderSourceQuery": "dolce perfume clearance"},
    )
    deal = candidate.to_normalized_deal()

    assert raw_price_signal(candidate, deal) is True


def test_raw_price_signal_rejects_unrelated_generic_product():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Generic Storage Bin",
        product_url="https://www.walmart.com/ip/2",
        current_price=12.99,
        variant_attributes={"finderSourceQuery": "storage clearance"},
    )
    deal = candidate.to_normalized_deal()

    assert raw_price_signal(candidate, deal) is False


def test_build_review_candidates_adds_raw_price_card_when_base_filter_would_hide_it():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz",
        product_url="https://www.walmart.com/ip/3",
        current_price=65.04,
        sku="123",
        variant_attributes={"finderSourceQuery": "dolce perfume clearance"},
    )

    result = build_review_candidate_cards_with_raw_leads([candidate], limit=5)

    assert len(result.cards) == 1
    assert getattr(result.cards[0], "raw_price_lead", False) is True
    assert getattr(result.cards[0], "manual_share_allowed", False) is True
    rendered = str(result.cards[0].embed.to_dict())
    assert "Raw price lead" in rendered
    assert "Manual review needed" in rendered
