from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider, walmart_config_from_env
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.routing import STAFF_REVIEW_ROUTE


def test_walmart_provider_disabled_until_explicitly_enabled():
    provider = WalmartProvider(WalmartAffiliateConfig())

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


def test_walmart_search_uses_page_and_price_sort(monkeypatch):
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    captured = {}

    def fake_request_json(url):
        captured["url"] = url
        return {"items": [], "totalResults": 80, "start": 11}

    monkeypatch.setattr(provider, "_request_json", fake_request_json)

    payload = provider._search(
        query="monitor",
        request=ProviderScanRequest(
            source_key="walmart",
            query="monitor",
            max_results=10,
            page=2,
            sort="price",
            order="ascending",
        ),
        page_size=10,
    )

    assert payload["totalResults"] == 80
    assert "query=monitor" in captured["url"]
    assert "numItems=10" in captured["url"]
    assert "start=11" in captured["url"]
    assert "sort=price" in captured["url"]
    assert "order=ascending" in captured["url"]


def test_walmart_ignores_suspicious_essential_reference_price():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 5164570594,
        "name": "Cottonelle Ultra Clean Toilet Paper, Strong Toilet Tissue, 6 Mega Rolls",
        "salePrice": 6.98,
        "msrp": 115.14,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=5164570594",
        "stock": "Available",
        "availableOnline": True,
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="toilet paper"))

    assert candidate is not None
    assert candidate.current_price == 6.98
    assert candidate.typical_price is None
    assert any("ignored suspicious Walmart msrp reference price" in signal for signal in candidate.signals)


def test_walmart_keeps_reasonable_reference_price_for_electronics():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 12345,
        "name": "Gaming Monitor 27 inch 144Hz",
        "salePrice": 99.0,
        "msrp": 179.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=12345",
        "stock": "Available",
        "availableOnline": True,
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="monitor"))

    assert candidate is not None
    assert candidate.typical_price == 179.99
    assert "Walmart reference price source: msrp" in candidate.signals


def test_walmart_flags_parent_ps5_title_when_priced_variant_is_xbox():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 9001,
        "name": "HyperX Cloud Gaming Headset for PS5",
        "salePrice": 19.99,
        "msrp": 99.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=9001",
        "stock": "Available",
        "availableOnline": True,
        "variantAttributes": {"platform": "Xbox", "color": "Black"},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="gaming headset"))
    assert candidate is not None
    assert candidate.platform == "Xbox"
    assert candidate.variant_label == "Xbox / Black"
    assert candidate.option_mismatch_warning is not None
    assert "PS5" in candidate.option_mismatch_warning
    assert "Xbox" in candidate.option_mismatch_warning

    decision = evaluate_candidate(candidate)
    assert decision.route.route == STAFF_REVIEW_ROUTE
    assert decision.should_alert is False
    assert decision.hold_for_review is True


def test_walmart_does_not_false_flag_parent_title_that_mentions_both_platforms():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 9003,
        "name": "HyperX Cloud Gaming Headset for PS5 and Xbox",
        "salePrice": 39.99,
        "msrp": 79.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=9003",
        "stock": "Available",
        "availableOnline": True,
        "variantAttributes": {"platform": "Xbox", "color": "Black"},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="gaming headset"))
    assert candidate is not None
    assert candidate.platform == "Xbox"
    assert candidate.option_mismatch_warning is None


def test_walmart_extracts_selected_variant_from_product_variants():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 9004,
        "name": "Gaming Headset",
        "salePrice": 29.99,
        "msrp": 89.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=9004",
        "stock": "Available",
        "availableOnline": True,
        "productVariants": [
            {"itemId": "9003", "platform": "PS5", "color": "White"},
            {"itemId": "9004", "platform": "Xbox", "color": "Black"},
        ],
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="gaming headset"))
    assert candidate is not None
    assert candidate.platform == "Xbox"
    assert candidate.color == "Black"
    assert candidate.variant_label == "Xbox / Black"


def test_walmart_flags_pack_size_mismatch_for_staff_review():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 9002,
        "name": "Laundry Detergent 12 Pack",
        "salePrice": 6.99,
        "msrp": 79.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=9002",
        "stock": "Available",
        "availableOnline": True,
        "variantAttributes": {"packSize": "2 pack"},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="detergent"))
    assert candidate is not None
    assert candidate.pack_size == "2 pack"
    assert candidate.option_mismatch_warning is not None
    assert "12 pack" in candidate.option_mismatch_warning
    assert "2 pack" in candidate.option_mismatch_warning

    decision = evaluate_candidate(candidate)
    assert decision.route.route == STAFF_REVIEW_ROUTE
    assert decision.should_alert is False
