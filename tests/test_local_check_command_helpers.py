from sniperplug.cogs.local_inventory import clean_optional, normalize_retailer_key


def test_normalize_retailer_aliases():
    assert normalize_retailer_key("home") == "home_depot"
    assert normalize_retailer_key("Home Depot") == "home_depot"
    assert normalize_retailer_key("home_depot") == "home_depot"
    assert normalize_retailer_key("hd") == "home_depot"
    assert normalize_retailer_key("Best Buy") == "bestbuy"


def test_clean_optional_strips_empty_values():
    assert clean_optional(None) is None
    assert clean_optional("") is None
    assert clean_optional("   ") is None
    assert clean_optional(" 12345 ") == "12345"
