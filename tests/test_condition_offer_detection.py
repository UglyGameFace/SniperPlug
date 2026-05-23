from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate, lower_price_condition_label


def test_lower_price_condition_label_detects_open_box_excellent():
    assert lower_price_condition_label("Open Box - Excellent", {}) == "Open Box - Excellent"


def test_lower_price_condition_label_detects_like_new_from_attrs():
    assert lower_price_condition_label(None, {"conditionLabel": "Used - Like New"}) == "Used - Like New"


def test_evaluate_candidate_marks_condition_specific_offer():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Gaming Headset",
        product_url="https://www.walmart.com/ip/123",
        current_price=39.99,
        typical_price=129.99,
        condition="Open Box - Excellent",
        product_id="123",
        product_id_type="sku",
        can_add_to_cart=True,
    )

    decision = evaluate_candidate(candidate)

    assert any("Selected condition offer" in reason for reason in decision.reasons)
    assert "Lower-price condition offer" in decision.deal.alert_tags
    assert any("Condition-specific lower price" in flag for flag in decision.deal.risk_flags)
