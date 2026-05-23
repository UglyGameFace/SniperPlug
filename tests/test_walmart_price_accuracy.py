from sniperplug.providers.walmart import _trusted_reference_price


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
