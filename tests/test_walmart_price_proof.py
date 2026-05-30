from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import _best_reference_context_price, _trusted_current_price, _trusted_reference_price, _walmart_promotion_proof
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


def test_walmart_accepts_nested_was_price_as_discount_proof():
    item = {"priceInfo": {"wasPrice": {"price": 129.99}}, "salePrice": 79.99}

    reference, signal = _trusted_reference_price(item, title="Gaming Keyboard", current_price=79.99)

    assert reference == 129.99
    assert signal == "Walmart reference price source: priceInfo.wasPrice"


def test_walmart_accepts_snake_case_original_price_as_discount_proof():
    item = {"price_info": {"original_price": "$49.99"}, "sale_price": "$24.99"}

    reference, signal = _trusted_reference_price(item, title="Gaming Mouse", current_price=24.99)

    assert reference == 49.99
    assert signal == "Walmart reference price source: price_info.original_price"


def test_walmart_keeps_low_trust_reference_as_context_not_discount():
    item = {"listPrice": 199.99, "salePrice": 99.99}

    reference, signal = _trusted_reference_price(item, title="Vacuum", current_price=99.99)
    context_price, context_source = _best_reference_context_price(item=item, current_price=99.99)

    assert reference is None
    assert "ignored low-confidence" in signal
    assert context_price == 199.99
    assert context_source == "listPrice"


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


def test_walmart_uses_real_sale_price_not_extra_savings_unit_values():
    item = {
        "name": "Astercook Kitchen Knife Set, 14 pcs Knives Block Set",
        "salePrice": 39.99,
        "wasPrice": 199.99,
        "extraSavings": {"amount": 5.0, "description": "Save $5.00 extra"},
        "unitPrice": "$2.86/count",
    }

    current, signal = _trusted_current_price(item)
    reference, reference_signal = _trusted_reference_price(item, title=item["name"], current_price=current)
    proof = _walmart_promotion_proof(item)

    assert current == 39.99
    assert signal == "Walmart current price source: salePrice"
    assert reference == 199.99
    assert reference_signal == "Walmart reference price source: wasPrice"
    assert proof["walmartCashSavings"] == "5.00"


def test_walmart_builds_was_from_product_savings_but_not_cash_bonus():
    item = {
        "name": "Dolce Gabbana The One Men Eau De Parfum Spray 3.3 oz",
        "salePrice": 61.20,
        "savingsAmount": 112.20,
        "walmartCashOffer": {"amount": 5.0, "description": "Earn $5 Walmart Cash"},
    }

    current, _ = _trusted_current_price(item)
    reference, reference_signal = _trusted_reference_price(item, title=item["name"], current_price=current)
    proof = _walmart_promotion_proof(item)

    assert current == 61.20
    assert reference == 173.40
    assert reference_signal == "Walmart reference price source: wasPriceFromSavings.savingsAmount"
    assert proof["walmartCashSavings"] == "5.00"
