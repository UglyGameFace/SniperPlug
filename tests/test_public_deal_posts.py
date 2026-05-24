from sniperplug.services.public_deal_posts import active_cache_key, canonical_url_key, public_post_key


def test_public_post_key_includes_price_so_lower_price_can_post_again():
    first = public_post_key(retailer="walmart", url="https://www.walmart.com/ip/123?x=1", current_price=49.99)
    lower = public_post_key(retailer="walmart", url="https://www.walmart.com/ip/123?x=1", current_price=39.99)

    assert first != lower
    assert first.endswith("price:49.99")
    assert lower.endswith("price:39.99")


def test_public_post_key_dedupes_same_offer_same_price():
    a = public_post_key(retailer="walmart", url="https://www.walmart.com/ip/123?aff=abc", current_price=49.99, selected_offer_id="offer-1")
    b = public_post_key(retailer="Walmart", url="https://www.walmart.com/ip/123?aff=xyz", current_price=49.99, selected_offer_id="offer-1")

    assert a == b


def test_active_cache_key_ignores_price_to_keep_latest_active_state():
    key = active_cache_key(retailer="walmart", url="https://www.walmart.com/ip/123?aff=abc", sku="123")

    assert key == "walmart:123"


def test_canonical_url_key_strips_tracking_query():
    assert canonical_url_key("https://www.walmart.com/ip/123?tag=abc") == "https://www.walmart.com/ip/123"
