from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.low_price_scout import score_candidate, scout_low_price_leads


def test_score_candidate_promotes_dolce_fragrance_low_price():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz",
        product_url="https://www.walmart.com/ip/1",
        current_price=65.04,
        seller_name="Walmart",
        stock_status="Available",
        variant_attributes={"finderSourceQuery": "dolce perfume clearance", "availableOnline": "true"},
    )

    lead = score_candidate(candidate)

    assert lead is not None
    assert lead.score >= 70
    assert any("hot brand" in reason for reason in lead.reasons)
    assert any("hot category" in reason for reason in lead.reasons)


def test_score_candidate_rejects_unrelated_low_price_product():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Generic Plastic Storage Bin",
        product_url="https://www.walmart.com/ip/2",
        current_price=6.99,
        variant_attributes={"finderSourceQuery": "clearance"},
    )

    assert score_candidate(candidate) is None


def test_scout_low_price_leads_returns_private_manual_share_cards():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz",
        product_url="https://www.walmart.com/ip/3",
        current_price=65.04,
        sku="123",
        seller_name="Walmart",
        stock_status="Available",
        variant_attributes={"finderSourceQuery": "dolce perfume clearance", "availableOnline": "true"},
    )

    cards = scout_low_price_leads([candidate], limit=5)

    assert len(cards) == 1
    assert getattr(cards[0], "low_price_scout", False) is True
    assert getattr(cards[0], "manual_share_allowed", False) is True
    assert getattr(cards[0], "should_alert", True) is False
    rendered = str(cards[0].embed.to_dict())
    assert "Low-price scout" in rendered
    assert "Scout score" in rendered
