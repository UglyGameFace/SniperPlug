from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth
from sniperplug.services.walmart_cash_offers import walmart_cash_search_terms


def test_walmart_cash_object_does_not_treat_price_or_item_id_as_cash_amount():
    item = {
        "itemId": 987654321,
        "price": 29.97,
        "walmartCash": {
            "eligible": True,
            "itemId": 987654321,
            "currentPrice": 29.97,
        },
    }
    assert extract_walmart_cash_api_truth(item, current_price=29.97) is None


def test_dedicated_walmart_cash_amount_field_is_accepted():
    item = {
        "itemId": "123",
        "manufacturerOffer": {
            "label": "Get Walmart Cash",
            "walmartCashAmount": 5.00,
        },
    }
    proof = extract_walmart_cash_api_truth(item, current_price=20.00)
    assert proof is not None
    assert proof.amount == 5.00


def test_exact_action_text_with_amount_is_accepted_but_nearby_price_is_not():
    valid = {"promotion": {"text": "Get $4.00 Walmart Cash after purchase"}}
    invalid = {"promotion": {"text": "Walmart Cash available", "currentPrice": "$24.98"}}
    assert extract_walmart_cash_api_truth(valid, current_price=24.98).amount == 4.00
    assert extract_walmart_cash_api_truth(invalid, current_price=24.98) is None


def test_product_catalog_discovery_is_disabled_without_supported_offer_feed():
    assert walmart_cash_search_terms(None) == ()
    assert walmart_cash_search_terms("manufacturer offers") == ()
    assert walmart_cash_search_terms("get walmart cash") == ()
    assert walmart_cash_search_terms("personal care") == ()
