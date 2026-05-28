from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.low_price_scout import score_candidate, score_search_intent


def test_exact_query_intent_promotes_the_one_over_other_dolce_products():
    exact = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz",
        product_url="https://www.walmart.com/ip/1",
        current_price=65.04,
        typical_price=153.00,
        seller_name="Walmart",
        stock_status="Available",
        variant_attributes={"finderSourceQuery": "dolce perfume clearance", "availableOnline": "true"},
    )
    wrong_line = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana Light Blue Eau De Toilette Spray 4.2 oz",
        product_url="https://www.walmart.com/ip/2",
        current_price=24.99,
        seller_name="Walmart",
        stock_status="Available",
        variant_attributes={"finderSourceQuery": "dolce perfume clearance", "availableOnline": "true"},
    )

    exact_lead = score_candidate(exact, search_query="dolce gabbana the one")
    wrong_lead = score_candidate(wrong_line, search_query="dolce gabbana the one")

    assert exact_lead is not None
    assert wrong_lead is not None
    assert exact_lead.score > wrong_lead.score
    assert any("exact search phrase" in reason for reason in exact_lead.reasons)
    assert any("missing distinctive term: one" in reason for reason in wrong_lead.reasons)


def test_search_intent_penalty_is_not_doubled_inside_intent_helper():
    score, reasons = score_search_intent("dolce gabbana light blue eau de toilette", "dolce gabbana the one")

    assert score < 0
    assert any("missing distinctive term: one" in reason for reason in reasons)
