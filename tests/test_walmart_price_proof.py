from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import _trusted_reference_price, _walmart_promotion_proof
from sniperplug.services.anomaly_score import has_suspicious_reference


def test_walmart_ignores_msrp_as_discount_proof():
    item = {"msrp": 94.99, "salePrice": 6.97}

    reference, signal = _trusted_reference_price(item, title="Turtle Wax 100 oz car wash", current_price=6.97)

    assert reference is None
    assert signal is not None
    assert "ignored low-confidence" in signal


def test_walmart_accepts_was_price_as_discount_proof():
    item = {"wasPrice": 94.99, "salePrice": 6.97}

    reference, signal = _trusted_reference_price(item, title="Gaming Headset", current_price=6.97)

    assert reference == 94.99
    assert signal == "Walmart reference price source: wasPrice"


def test_walmart_detects_coupon_and_cash_payload_values():
    item = {
        "coupon": {"amount": 5.0, "description": "Clip $5 coupon"},
        "walmartCashOffer": {"amount": 3.0, "description": "Earn $3 Walmart Cash reward"},
    }

    proof = _walmart_promotion_proof(item)

    assert proof["couponSavings"] == "5.00"
    assert proof["walmartCashSavings"] == "3.00"


def test_candidate_carries_coupon_and_walmart_cash_to_deal():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Coupon item",
        product_url="https://www.walmart.com/ip/123",
        current_price=20.00,
        typical_price=None,
        variant_attributes={"couponSavings": "5.00", "walmartCashSavings": "3.00", "referencePriceTrusted": "no"},
    )

    deal = candidate.to_normalized_deal()

    assert deal.pre_coupon_price == 25.00
    assert deal.coupon_savings == 5.00
    assert "Walmart Coupon" in deal.alert_tags
    assert "Walmart Cash" in deal.alert_tags
    assert has_suspicious_reference(deal) is True
