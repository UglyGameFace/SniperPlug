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
    assert candidate.current_price == 5.03
    assert candidate.sku == "1001234567"
    assert candidate.stock_status == "Limited stock"
    assert any(".03" in signal for signal in candidate.signals)
