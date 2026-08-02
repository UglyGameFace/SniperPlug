from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth
from sniperplug.services.walmart_cash_offers import walmart_cash_search_terms


def test_extracts_walmart_cash_amount_from_explicit_api_object():
    item = {
        "name": "Detergent",
        "salePrice": 9.99,
        "walmartCashOffer": {
            "amount": 8,
            "description": "Earn $8 Walmart Cash reward",
        },
    }

    proof = extract_walmart_cash_api_truth(item, current_price=9.99)

    assert proof is not None
    assert proof.amount == 8
    assert "walmartCashOffer" in proof.proof_path
    assert "Walmart Cash" in proof.signal()


def test_rejects_walmart_cash_eligibility_without_amount():
    item = {
        "name": "Baby wipes",
        "salePrice": 12.99,
        "badges": [{"text": "Walmart Cash eligible"}],
    }

    assert extract_walmart_cash_api_truth(item, current_price=12.99) is None


def test_extracts_walmart_cash_badge_when_amount_is_present():
    item = {
        "name": "Baby wipes",
        "salePrice": 12.99,
        "badges": [{"text": "Earn $5 Walmart Cash"}],
    }

    proof = extract_walmart_cash_api_truth(item, current_price=12.99)

    assert proof is not None
    assert proof.amount == 5


def test_blocks_onepay_cashback_as_walmart_cash():
    item = {
        "name": "Soap",
        "salePrice": 6.99,
        "onePayCashRewards": "Earn up to 5% cash back with OnePay",
    }

    assert extract_walmart_cash_api_truth(item, current_price=6.99) is None


def test_blocks_buy_more_save_promo_as_walmart_cash():
    item = {
        "name": "Toy",
        "salePrice": 18.99,
        "promotionText": "Buy more, save up to $10",
    }

    assert extract_walmart_cash_api_truth(item, current_price=18.99) is None


def test_default_walmart_cash_search_uses_many_product_departments():
    routes = walmart_cash_search_terms("walmart cash offers")

    assert len(routes) >= 6
    assert "personal care" in routes
    assert "laundry detergent" in routes
    assert all("walmart cash" not in route.lower() for route in routes)


def test_custom_cash_query_removes_promo_words_before_walmart_search():
    assert walmart_cash_search_terms("tide walmart cash offers") == ("tide",)
