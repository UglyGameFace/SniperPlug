from __future__ import annotations


def test_walmart_cash_modules_import_cleanly():
    import sniperplug.services.walmart_cash_offers as offers
    import sniperplug.services.walmart_cash_pipeline as pipeline
    import sniperplug.services.walmart_promo_classifier as classifier

    assert hasattr(offers, "build_walmart_cash_summary_embed")
    assert hasattr(offers, "find_walmart_cash_offer")
    assert hasattr(offers, "build_walmart_api_probe_embed")
    assert hasattr(classifier, "classify_walmart_promos")
    assert pipeline is not None


def test_walmart_promo_classifier_export_uses_truth_buckets():
    from sniperplug.services.walmart_promo_classifier import classify_walmart_promos

    item = {
        "itemId": "123",
        "name": "Detergent",
        "salePrice": 9.99,
        "promotions": [
            {"type": "WALMART_CASH", "description": "Earn $5 Walmart Cash", "amount": 5},
            {"text": "Buy more, save up to $10 | View eligible items"},
        ],
        "onePayCashRewards": "Earn up to 5% cash back with OnePay",
        "rollBack": True,
        "clearance": True,
    }

    buckets = classify_walmart_promos(item, current_price=9.99)

    assert buckets["walmart_cash"]
    assert buckets["walmart_cash"][0]["amount"] == 5
    assert buckets["cart_promo"]
    assert buckets["onepay"]
    assert buckets["markdown"] or buckets["clearance"]
    assert "onepay" not in buckets["walmart_cash"][0]["value"].lower()
