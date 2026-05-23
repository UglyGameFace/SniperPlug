from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartProvider, _trusted_reference_price


def test_turtle_wax_msrp_does_not_create_fake_ninety_percent_glitch():
    item = {
        "name": "Turtle Wax 50597 Max-Power 3 Levels of Cleaning Car Wash, 100 oz",
        "salePrice": 6.97,
        "msrp": 94.99,
        "offerType": "ONLINE_AND_STORE",
    }

    reference, signal = _trusted_reference_price(item, item["name"], 6.97)

    assert reference is None
    assert signal == "ignored suspicious Walmart msrp reference price: $94.99"


def test_explicit_was_price_can_drive_real_discount():
    item = {
        "name": "Gaming Monitor 27 inch",
        "salePrice": 149.0,
        "wasPrice": 299.0,
        "msrp": 999.0,
    }

    reference, signal = _trusted_reference_price(item, item["name"], 149.0)

    assert reference == 299.0
    assert signal == "Walmart reference price source: wasPrice"


def test_low_trust_list_price_ignored_for_cheap_consumable():
    item = {
        "name": "Laundry Detergent 100 oz",
        "salePrice": 8.0,
        "listPrice": 80.0,
    }

    reference, signal = _trusted_reference_price(item, item["name"], 8.0)

    assert reference is None
    assert signal == "ignored suspicious Walmart listPrice reference price: $80.00"


def test_walmart_candidate_carries_selected_seller_and_fulfillment():
    item = {
        "name": "Gaming Headset",
        "itemId": 12345,
        "salePrice": 24.99,
        "wasPrice": 99.99,
        "sellerName": "Deal Outlet LLC",
        "sellerId": "seller-123",
        "marketplace": True,
        "fulfillmentType": "MARKETPLACE",
        "condition": "New",
        "stock": "Available",
        "availableOnline": True,
    }

    candidate = WalmartProvider(configured=True)._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="headset"))
    deal = candidate.to_normalized_deal()

    assert candidate.seller_name == "Deal Outlet LLC"
    assert candidate.fulfillment_type == "MARKETPLACE"
    assert candidate.condition == "New"
    assert candidate.variant_attributes["seller"] == "Deal Outlet LLC"
    assert candidate.variant_attributes["walmartSeller"] == "no"
    assert "selected offer may be third-party seller" in candidate.signals
    assert deal.seller_name == "Deal Outlet LLC"
    assert "Selected offer seller: Deal Outlet LLC" in deal.verification_notes
