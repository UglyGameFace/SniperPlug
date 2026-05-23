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
