from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.serpapi_home_depot import SerpApiHomeDepotConfig, SerpApiHomeDepotProvider


def test_serpapi_home_depot_health_disabled_without_key():
    import asyncio

    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key=None))
    health = asyncio.run(provider.healthcheck())

    assert health.ok is False
    assert health.status == ProviderStatus.DISABLED


def test_serpapi_home_depot_maps_products_to_candidates():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Milwaukee Drill Clearance",
                "product_id": "1001234567",
                "link": "https://www.homedepot.com/p/1001234567",
                "price": "$5.03",
                "thumbnail": "https://example.com/drill.jpg",
                "store_stock": {"status": "Limited stock"},
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(
            source_key="home_depot_serpapi",
            query="milwaukee drill",
            metadata={"store_id": "6237", "zip_code": "06610"},
        ),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.retailer == "Home Depot"
    assert candidate.title == "Milwaukee Drill Clearance"
    assert candidate.product_url == "https://www.homedepot.com/p/1001234567"
    assert candidate.current_price == 5.03
    assert candidate.sku == "1001234567"
    assert candidate.stock_status == "Limited stock"
    assert any(".03" in signal for signal in candidate.signals)


def test_serpapi_home_depot_normalizes_api_online_links():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Glacier Bay Vanity",
                "product_id": "203486567",
                "link": "http://apionline.homedepot.com/p/Glacier-Bay-Vanity/203486567",
                "price": "$159.00",
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(source_key="home_depot_serpapi", query="vanity"),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.product_url == "https://www.homedepot.com/p/Glacier-Bay-Vanity/203486567"
    assert any("product link normalized" in signal for signal in candidate.signals)


def test_serpapi_home_depot_reads_nested_price_fields():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Ceiling Fan",
                "product_id": "123",
                "link": "https://www.homedepot.com/p/123",
                "primary_offer": {"price": "$24.03"},
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(source_key="home_depot_serpapi", query="fan"),
    )

    assert len(candidates) == 1
    assert candidates[0].current_price == 24.03


def test_serpapi_home_depot_extracts_was_typical_price_from_top_level_fields():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Collette 48 in. Vanity",
                "product_id": "327191749",
                "link": "https://www.homedepot.com/p/327191749",
                "price": "$1,139.00",
                "was_price": "$1,899.00",
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(source_key="home_depot_serpapi", query="vanity"),
    )

    assert len(candidates) == 1
    assert candidates[0].current_price == 1139.0
    assert candidates[0].typical_price == 1899.0
    assert any("was/typical price returned" in signal for signal in candidates[0].signals)


def test_serpapi_home_depot_extracts_was_typical_price_from_nested_offer_fields():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Special Buy Vanity",
                "product_id": "555",
                "link": "https://www.homedepot.com/p/555",
                "primary_offer": {
                    "price": "$499.00",
                    "original_price": "$799.00",
                },
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(source_key="home_depot_serpapi", query="vanity"),
    )

    assert len(candidates) == 1
    assert candidates[0].current_price == 499.0
    assert candidates[0].typical_price == 799.0


def test_serpapi_home_depot_ignores_typical_price_lower_than_current_price():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Normal Item",
                "product_id": "777",
                "link": "https://www.homedepot.com/p/777",
                "price": "$100.00",
                "was_price": "$90.00",
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(source_key="home_depot_serpapi", query="normal"),
    )

    assert len(candidates) == 1
    assert candidates[0].current_price == 100.0
    assert candidates[0].typical_price is None


def test_serpapi_home_depot_maps_advantage_fields_to_variant_attributes_and_signals():
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key="fake"))
    payload = {
        "products": [
            {
                "title": "Collette Vanity",
                "product_id": "327191749",
                "link": "https://www.homedepot.com/p/327191749",
                "price": "$1,139.00",
                "price_was": "$1,899.00",
                "price_saving": "$760.00",
                "percentage_off": "40%",
                "price_badge": "Special Buy",
                "brand": "Home Decorators Collection",
                "model_number": "CL48CO-WH",
                "rating": 4.1,
                "reviews": 299,
                "delivery": {"free_delivery": True, "scheduled_delivery": False},
                "pickup": {"free_pickup": True},
                "stock_information": {
                    "store_stock": "3",
                    "store_stock_status": "Limited Stock",
                    "general_stock_status": "In Stock",
                },
                "add_to_cart": True,
                "thumbnail": "https://example.com/vanity.jpg",
            }
        ]
    }

    candidates = provider._candidates_from_payload(
        payload,
        ProviderScanRequest(source_key="home_depot_serpapi", query="vanity"),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.typical_price == 1899.0
    assert candidate.image_url == "https://example.com/vanity.jpg"
    assert candidate.model == "CL48CO-WH"
    assert candidate.can_add_to_cart is True
    assert candidate.stock_status == "Store stock: 3 (Limited Stock)"
    assert candidate.variant_attributes["brand"] == "Home Decorators Collection"
    assert candidate.variant_attributes["price_saving"] == "$760.00"
    assert candidate.variant_attributes["percentage_off"] == "40%"
    assert candidate.variant_attributes["price_badge"] == "Special Buy"
    assert candidate.variant_attributes["delivery"] == "Delivery: free delivery"
    assert candidate.variant_attributes["pickup"] == "Pickup: free pickup"
    assert any("Home Depot saving: $760.00" in signal for signal in candidate.signals)
    assert any("Home Depot badge: Special Buy" in signal for signal in candidate.signals)
