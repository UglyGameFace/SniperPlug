from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_review_candidates import build_review_candidate_cards


def test_review_candidate_with_context_reference_is_shown_private_review():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Review item",
        product_url="https://www.walmart.com/ip/1",
        current_price=20.0,
        typical_price=None,
        sku="1",
        variant_attributes={"referenceContextPrice": "80.00", "referenceContextSource": "listPrice"},
        signals=("rollback",),
    )
    result = build_review_candidate_cards([candidate])
    assert len(result.cards) == 1
    assert result.weak_reference_count == 1


def test_review_candidate_filters_plain_products():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Normal item",
        product_url="https://www.walmart.com/ip/2",
        current_price=20.0,
        typical_price=None,
        sku="2",
        variant_attributes={},
        signals=(),
    )
    result = build_review_candidate_cards([candidate])
    assert result.cards == []
    assert result.no_value_signal_count == 1
