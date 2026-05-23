from sniperplug.services.public_posting import parse_retailer_list, retailer_credit_note


def test_parse_retailer_list_normalizes_supported_aliases():
    assert parse_retailer_list("walmart, home, best buy, amz") == ("walmart", "home_depot", "bestbuy", "amazon")


def test_parse_retailer_list_dedupes_and_ignores_unknowns():
    assert parse_retailer_list("walmart, walmart, target, hd") == ("walmart", "home_depot")


def test_credit_note_warns_for_limited_credit_retailers():
    assert "Limited/paid quota" in retailer_credit_note("home_depot")
    assert "Limited/paid quota" in retailer_credit_note("amazon")
