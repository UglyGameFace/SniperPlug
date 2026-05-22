from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider, walmart_config_from_env


def test_walmart_provider_disabled_until_explicitly_enabled():
    provider = WalmartProvider(WalmartAffiliateConfig())

    # async methods are simple enough to run through asyncio in project tests.
    import asyncio

    health = asyncio.run(provider.healthcheck())

    assert health.ok is False
    assert health.status == ProviderStatus.DISABLED


def test_walmart_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("WALMART_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("WALMART_CONSUMER_ID", "cid")
    monkeypatch.setenv("WALMART_KEY_VERSION", "1")
    monkeypatch.setenv("WALMART_PRIVATE_KEY_B64", "key")
    monkeypatch.setenv("WALMART_PUBLISHER_ID", "")

    config = walmart_config_from_env()

    assert config.enabled is True
    assert config.consumer_id == "cid"
    assert config.key_version == "1"
    assert config.private_key_b64 == "key"
    assert config.publisher_id is None


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


def test_pubid_placeholder_falls_back_to_direct_walmart_link():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 516833054,
        "name": "Sceptre 32 inch Smart TV",
        "salePrice": 108.0,
        "productTrackingUrl": "https://goto.walmart.com/c/|PUBID|/568844/9383?prodsku=516833054",
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="tv"))

    assert candidate is not None
    assert candidate.product_url == "https://www.walmart.com/ip/516833054"
    assert "tracking link unavailable; direct Walmart link used" in candidate.signals
