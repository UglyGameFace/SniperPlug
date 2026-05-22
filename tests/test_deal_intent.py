from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.deal_intent import apply_deal_intent_signals, assess_deal_intent
from sniperplug.services.anomaly_score import score_deal_anomaly


def test_closeout_fitment_deal_is_not_forced_into_glitch_bucket():
    deal = NormalizedDeal(
        title="Bridgestone Alenza Sport A/S Special/Closeout older production date tire set of 4",
        retailer="TireRack",
        product_url="https://example.com/tire",
        current_price=71.13,
        typical_price=388.09,
        availability_message="In Stock; Production Year: 2023; per tire; set of 4",
    )
    deal.recalculate_prices()

    assessment = assess_deal_intent(deal)

    assert assessment.primary_intent == "legit_closeout_or_clearance"
    assert "🏷️ Closeout / Clearance" in assessment.labels
    assert "🚗 Fitment-Sensitive" in assessment.labels
    assert "📦 Quantity / Pack Check" in assessment.labels
    assert not assessment.staff_review_recommended


def test_unexplained_extreme_discount_routes_to_possible_price_error():
    deal = NormalizedDeal(
        title="High-end gaming laptop",
        retailer="Example Store",
        product_url="https://example.com/laptop",
        current_price=9.99,
        typical_price=1299.99,
        availability_message="In Stock",
    )
    deal.recalculate_prices()

    assessment = assess_deal_intent(deal)

    assert assessment.primary_intent == "possible_price_error"
    assert assessment.staff_review_recommended

    apply_deal_intent_signals(deal)
    assert deal.is_possible_price_error is True
    assert deal.risk_level == "high"


def test_vendor_promo_keeps_intent_separate_from_price_error():
    deal = NormalizedDeal(
        title="DeWalt tool bundle instant savings manufacturer rebate",
        retailer="Home Depot",
        product_url="https://example.com/tools",
        current_price=149.00,
        typical_price=249.00,
        availability_message="Coupon and manufacturer rebate may apply",
    )
    deal.recalculate_prices()

    assessment = assess_deal_intent(deal)

    assert assessment.primary_intent == "supplier_or_vendor_promo"
    assert "🏭 Supplier / Vendor Promo" in assessment.labels
    assert not assessment.staff_review_recommended


def test_intent_boost_feeds_anomaly_score_reasons():
    deal = NormalizedDeal(
        title="Case of office supplies clearance pack of 24",
        retailer="Example Store",
        product_url="https://example.com/bulk",
        current_price=19.99,
        typical_price=99.99,
        availability_message="clearance case pack",
    )
    deal.recalculate_prices()

    score = score_deal_anomaly(deal)

    assert score.score >= 60
    assert any(reason.startswith("Intent:") for reason in score.reasons)
