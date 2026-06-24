from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth


def test_rejects_search_route_text_without_cash_offer_field():
    item = {
        "query": "walmart cash offers",
        "name": "Toy car that appeared in Walmart Cash search",
        "salePrice": 18.99,
        "priceInfo": {"currentPrice": 18.99},
    }

    assert extract_walmart_cash_api_truth(item, current_price=18.99) is None


def test_rejects_product_title_with_walmart_cash_words():
    item = {
        "name": "Walmart Cash style coupon organizer",
        "salePrice": 9.99,
    }

    assert extract_walmart_cash_api_truth(item, current_price=9.99) is None


def test_rejects_buy_more_save_more_promo():
    item = {
        "name": "Toy",
        "salePrice": 18.99,
        "promotions": [{"text": "Buy more, save up to $10 | View eligible items"}],
    }

    assert extract_walmart_cash_api_truth(item, current_price=18.99) is None


def test_rejects_walmart_cash_eligible_without_amount():
    item = {
        "name": "Baby wipes",
        "salePrice": 12.99,
        "badges": [{"text": "Walmart Cash eligible"}],
    }

    assert extract_walmart_cash_api_truth(item, current_price=12.99) is None


def test_accepts_explicit_walmart_cash_amount_field():
    item = {
        "name": "Detergent",
        "salePrice": 9.99,
        "walmartCashOffer": {
            "amount": 8,
            "description": "Earn $8 Walmart Cash",
        },
    }

    proof = extract_walmart_cash_api_truth(item, current_price=9.99)

    assert proof is not None
    assert proof.amount == 8
    assert proof.proof_path == "walmartCashOffer"


def test_accepts_promo_object_with_walmart_cash_amount_text():
    item = {
        "name": "Toothpaste",
        "salePrice": 5.99,
        "promotions": [{"description": "Earn $3 Walmart Cash with this item"}],
    }

    proof = extract_walmart_cash_api_truth(item, current_price=5.99)

    assert proof is not None
    assert proof.amount == 3
