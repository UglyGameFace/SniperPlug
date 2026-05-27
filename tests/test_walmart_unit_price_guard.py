from sniperplug.providers.walmart import _price_from_path, _trusted_current_price


def test_current_price_does_not_use_unit_price_nested_price():
    item = {
        "unitPrice": {"price": 1.00},
        "priceInfo": {"unitPrice": {"price": 1.00}},
        "salePrice": 12.59,
    }

    value, source = _trusted_current_price(item)

    assert value == 12.59
    assert source == "Walmart current price source: salePrice"


def test_price_from_path_blocks_unit_price_by_default():
    item = {"priceInfo": {"unitPrice": {"price": 1.05}}}

    assert _price_from_path(item, "priceInfo.unitPrice.price") is None


def test_price_from_path_can_read_unit_price_when_explicitly_allowed_for_display_attrs():
    item = {"unitPrice": {"price": 1.05}}

    assert _price_from_path(item, "unitPrice.price", allow_unit_price=True) == 1.05
