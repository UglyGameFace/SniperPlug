from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.cached_walmart import _request_cache_payload, _result_from_cache


def test_walmart_scan_cache_scope_includes_location_metadata():
    request = ProviderScanRequest(
        source_key="walmart",
        query="detergent",
        page=1,
        max_results=10,
        metadata={"zip_code": "06108", "store_id": "1234", "requested_by": "99"},
    )

    payload = _request_cache_payload(request)

    assert payload["scope"] == {"zip_code": "06108", "store_id": "1234"}
    assert "requested_by" not in payload


def test_walmart_cached_result_ignores_unknown_candidate_fields():
    cached = {
        "provider_key": "walmart",
        "candidates": [
            {
                "source_key": "walmart",
                "retailer": "Walmart",
                "title": "Cached Item",
                "product_url": "https://www.walmart.com/ip/123",
                "unexpected_old_or_future_field": "ignored",
            }
        ],
    }

    result = _result_from_cache(cached)

    assert len(result.candidates) == 1
    assert result.candidates[0].title == "Cached Item"
