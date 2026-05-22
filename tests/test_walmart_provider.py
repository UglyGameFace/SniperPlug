from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider


def test_walmart_provider_disabled_until_explicitly_enabled():
    provider = WalmartProvider(WalmartAffiliateConfig())

    # async methods are simple enough to run through asyncio in project tests.
    import asyncio

    health = asyncio.run(provider.healthcheck())

    assert health.ok is False
    assert health.status == ProviderStatus.DISABLED


def test_walmart_payload_maps_to_source_candidate():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 516833054,
        "name": "Sceptre 32 inch Smart TV",
        "salePrice": 108.0,
        "msrp": 179.99,
        "upc": "792343232896",
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=516833054",
        "largeImage": "https://example.com/tv.jpg",
        "stock": "Available",
        "availableOnline": True,
        "clearance": True,
        "rollBack": True,
        "specialBuy": False,
        "categoryPath": "Home Page/Electronics/TV & Video/All TVs",
        "maxItemsInOrder": 2,
        "offerType": "ONLINE_ONLY",
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="tv"))

    assert candidate is not None
    assert candidate.retailer == "Walmart"
    assert candidate.title == "Sceptre 32 inch Smart TV"
    assert candidate.current_price == 108.0
    assert candidate.typical_price == 179.99
    assert candidate.sku == "516833054"
    assert candidate.upc == "792343232896"
    assert candidate.can_add_to_cart is True
    assert "clearance" in candidate.signals
    assert "rollback" in candidate.signals
    assert "max order quantity: 2" in candidate.signals
