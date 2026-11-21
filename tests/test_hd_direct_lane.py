"""Direct Home Depot gateway lane: identity, client breaker, composite
fallback/quota behaviour, and parsers fed with real captured gateway
response shapes (Nov 2026 POC; the gateway is cookieless/keyless by
design, so fixtures carry no secrets)."""

from __future__ import annotations

import asyncio
import time

import pytest

from sniperplug.providers.base import ProviderScanRequest, ProviderScanResult
from sniperplug.providers.home_depot_direct import (
    DirectHomeDepotProvider,
    HomeDepotLaneProvider,
)
from sniperplug.services import hd_direct_client
from sniperplug.services.hd_direct_client import (
    DirectBreaker,
    HdDirectBlockedError,
    HdDirectError,
)
from sniperplug.services.hd_direct_identity import (
    OP_HEADER_ATTEMPTS,
    default_chrome_major,
    identity_for,
    sweep_majors,
)
from sniperplug.services.home_depot_product_lookup import (
    _detail_from_direct_payload,
    fetch_home_depot_product_detail,
)

# Real searchModel product shape, captured live 2026-11-21 (sanitized:
# the gateway needs no cookies or keys, so nothing sensitive exists).
SEARCH_PAYLOAD = {
    "data": {
        "searchModel": {
            "metadata": {"totalProducts": 4353},
            "searchReport": {"totalProducts": 4353},
            "products": [
                {
                    "itemId": "325479354",
                    "identifiers": {
                        "productType": "PRODUCT",
                        "canonicalUrl": "/p/Milwaukee-M18-18V-Lithium-Ion-Brushless-Cordless-1-2-in-Compact-Drill-Driver-Kit/325479354",
                        "productLabel": "M18 18V Lithium-Ion Brushless Cordless 1/2 in. Compact Drill/Driver Kit",
                        "brandName": "Milwaukee",
                        "modelNumber": "3601-22CT",
                        "storeSkuNumber": "1009516338",
                    },
                    "pricing": {"value": 99.0, "original": 159.0, "promotion": None, "clearance": None},
                    "media": {"images": [{"url": "https://images.thdstatic.com/productImages/abc/svn/milwaukee-<SIZE>.jpg", "type": "IMAGE"}]},
                    "ratingsAndReviews": {"averageRating": 4.8, "totalReviews": 1234},
                    "fulfillment": {
                        "anchorStoreStatusType": "in_stock",
                        "fulfillmentOptions": [
                            {
                                "fulfillable": True,
                                "type": "pickup",
                                "services": [
                                    {
                                        "type": "BOPIS",
                                        "locations": [
                                            {"locationId": "6237", "isAnchor": True, "inventory": {"quantity": 7, "isInStock": True, "isOutOfStock": False}}
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    "badges": [{"name": "Special Buy"}],
                    "availabilityType": {"type": "STANDARD", "discontinued": False},
                },
                {
                    "itemId": "200000001",
                    "identifiers": {"productLabel": "No Price Widget", "canonicalUrl": "/p/x/200000001"},
                    "pricing": {"value": None, "original": None},
                    "media": {"images": []},
                    "fulfillment": {"fulfillmentOptions": []},
                },
            ],
        }
    }
}

PRODUCT_PAYLOAD = {
    "data": {
        "product": {
            "itemId": "325479354",
            "identifiers": {
                "canonicalUrl": "/p/Milwaukee-M18-Drill/325479354",
                "brandName": "Milwaukee",
                "productLabel": "M18 18V Compact Drill/Driver Kit",
                "storeSkuNumber": "1009516338",
                "upc": "045242123456",
            },
            "pricing": {"value": 0.03, "original": 159.0, "unitOfMeasure": "each", "clearance": {"value": 0.03, "percentageOff": 99.98}},
            "fulfillment": {
                "anchorStoreStatusType": "CLEARANCE",
                "fulfillmentOptions": [
                    {
                        "fulfillable": True,
                        "type": "pickup",
                        "services": [
                            {"type": "BOPIS", "locations": [{"locationId": "6237", "inventory": {"quantity": 3, "isInStock": True}}]}
                        ],
                    },
                    {
                        "fulfillable": True,
                        "type": "delivery",
                        "services": [
                            {"type": "STH", "locations": [{"locationId": "6237", "inventory": {"quantity": 12, "isInStock": True}}]}
                        ],
                    },
                ],
            },
            "media": {"images": [{"url": "https://images.thdstatic.com/productImages/abc/svn/milwaukee-<SIZE>.jpg"}]},
        }
    }
}


@pytest.fixture(autouse=True)
def _fresh_breaker(monkeypatch):
    monkeypatch.setattr(hd_direct_client, "_BREAKER", DirectBreaker(threshold=3, cooldown_seconds=6 * 3600))
    monkeypatch.delenv("SNIPERPLUG_HD_DIRECT_ENABLED", raising=False)
    yield


# ---------------------------------------------------------------------------
# Identity module
# ---------------------------------------------------------------------------


def test_identity_is_one_consistent_version_everywhere():
    for major in sweep_majors():
        identity = identity_for(major)
        assert identity.impersonate == f"chrome{major}"
        assert f"Chrome/{major}.0.0.0" in identity.user_agent
        assert f'"Google Chrome";v="{major}"' in identity.sec_ch_ua
        assert f'"Chromium";v="{major}"' in identity.sec_ch_ua


def test_default_identity_tracks_curl_cffi_chrome_target():
    from curl_cffi.requests.impersonate import DEFAULT_CHROME

    assert identity_for(default_chrome_major()).impersonate == str(DEFAULT_CHROME)


def test_headers_always_have_origin_referer_and_never_cookies():
    identity = identity_for(default_chrome_major())
    for opname in OP_HEADER_ATTEMPTS:
        for attempt in range(len(OP_HEADER_ATTEMPTS[opname])):
            headers = identity.headers(opname, attempt, referer="https://www.homedepot.com/s/drill")
            assert headers["Origin"] == "https://www.homedepot.com"
            assert headers["Referer"].startswith("https://www.homedepot.com")
            assert "cookie" not in {k.lower() for k in headers}
            assert headers["x-experience-name"]
            assert headers["apollographql-client-name"]


def test_search_model_header_attempts_cover_both_measured_captures():
    attempts = OP_HEADER_ATTEMPTS["searchModel"]
    assert ("search", "search") in attempts
    assert ("general-merchandise", "general-merchandise") in attempts


# ---------------------------------------------------------------------------
# Client: sweep, breaker, error classification
# ---------------------------------------------------------------------------


def _ok_payload():
    return 200, {"data": {"searchModel": {"products": []}}}


def test_client_success_records_working_identity(monkeypatch):
    calls = []

    def fake_attempt(major, attempt_index, opname, body, referer):
        calls.append((major, attempt_index))
        return _ok_payload()

    monkeypatch.setattr(hd_direct_client, "_execute_attempt", fake_attempt)
    payload = hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    assert payload["data"]["searchModel"] == {"products": []}
    assert len(calls) == 1
    assert hd_direct_client.breaker().working_major == calls[0][0]
    assert hd_direct_client.breaker().consecutive_blocks == 0


def test_client_206_sweeps_profiles_then_blocks(monkeypatch):
    seen = []

    def fake_attempt(major, attempt_index, opname, body, referer):
        seen.append(major)
        return 206, {"data": {"Generic Errors API": None}, "error": [{"message": "Generic Errors  API errors"}]}

    monkeypatch.setattr(hd_direct_client, "_execute_attempt", fake_attempt)
    with pytest.raises(HdDirectBlockedError):
        hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    # One attempt per swept profile (plus extra header attempts only on
    # the first profile): never a retry storm.
    assert len(seen) == len(set(seen)) or len(seen) <= len(sweep_majors()) + 1
    assert set(seen) == set(sweep_majors())
    assert hd_direct_client.breaker().consecutive_blocks == 1


def test_breaker_opens_after_threshold_and_skips_transport(monkeypatch):
    monkeypatch.setattr(hd_direct_client, "_BREAKER", DirectBreaker(threshold=2, cooldown_seconds=3600))
    calls = []

    def fake_attempt(major, attempt_index, opname, body, referer):
        calls.append(major)
        return 403, None

    monkeypatch.setattr(hd_direct_client, "_execute_attempt", fake_attempt)
    for _ in range(2):
        with pytest.raises(HdDirectBlockedError):
            hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    assert hd_direct_client.breaker().is_open()
    calls_before = len(calls)
    with pytest.raises(HdDirectBlockedError):
        hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    assert len(calls) == calls_before  # breaker open: zero gateway requests


def test_breaker_recovers_after_cooldown(monkeypatch):
    breaker = DirectBreaker(threshold=1, cooldown_seconds=60)
    breaker.record_blocked(206)
    assert breaker.is_open()
    breaker.open_until = time.time() - 1  # cooldown elapsed
    assert not breaker.is_open()
    monkeypatch.setattr(hd_direct_client, "_BREAKER", breaker)
    monkeypatch.setattr(hd_direct_client, "_execute_attempt", lambda *a: _ok_payload())
    payload = hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    assert payload["data"]
    assert breaker.consecutive_blocks == 0


def test_graphql_validation_error_is_not_a_block(monkeypatch):
    def fake_attempt(major, attempt_index, opname, body, referer):
        return 200, {"errors": [{"message": "Cannot query field"}], "data": None}

    monkeypatch.setattr(hd_direct_client, "_execute_attempt", fake_attempt)
    with pytest.raises(HdDirectError) as excinfo:
        hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    assert not isinstance(excinfo.value, HdDirectBlockedError)
    assert hd_direct_client.breaker().consecutive_blocks == 0


def test_http_500_is_a_failure_not_a_block(monkeypatch):
    monkeypatch.setattr(hd_direct_client, "_execute_attempt", lambda *a: (500, None))
    with pytest.raises(HdDirectError) as excinfo:
        hd_direct_client.graphql_sync("searchModel", "q", {}, referer="https://www.homedepot.com/s/x")
    assert not isinstance(excinfo.value, HdDirectBlockedError)
    assert hd_direct_client.breaker().consecutive_blocks == 0


# ---------------------------------------------------------------------------
# Direct provider parsing
# ---------------------------------------------------------------------------


def test_direct_provider_parses_real_search_shape(monkeypatch):
    async def fake_search_model(query, *, zip_code, store_id, page=1, page_size=24):
        assert zip_code == "06484"
        return SEARCH_PAYLOAD

    monkeypatch.setattr(hd_direct_client, "search_model", fake_search_model)
    provider = DirectHomeDepotProvider()
    result = asyncio.run(
        provider.scan(
            ProviderScanRequest(
                source_key="home_depot_serpapi",
                query="milwaukee drill",
                max_results=24,
                metadata={"zip_code": "06484"},
            )
        )
    )
    assert result.total_results == 4353
    assert result.metadata["lane"] == "direct"
    assert result.metadata["serpapi_credit"] == 0
    assert len(result.candidates) == 2
    drill, no_price = result.candidates
    assert drill.title.startswith("M18 18V")
    assert drill.current_price == 99.0
    assert drill.typical_price == 159.0
    assert drill.product_id == "325479354"
    assert drill.sku == "1009516338"
    assert drill.model == "3601-22CT"
    assert drill.variant_attributes["internet_number"] == "325479354"
    assert drill.variant_attributes["fulfillment_quantity"] == "7"
    assert "<SIZE>" not in (drill.image_url or "")
    assert "400" in (drill.image_url or "")
    assert no_price.current_price is None  # kept as a lead, price unknown


# ---------------------------------------------------------------------------
# Composite: fallback + quota accounting
# ---------------------------------------------------------------------------


class _FakeDirect:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = 0

    async def scan(self, request):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class _FakeSerpApi:
    def __init__(self, cache_hit=False):
        self.cache_hit = cache_hit
        self.requests = []

    async def scan(self, request):
        self.requests.append(request)
        return ProviderScanResult(
            provider_key="home_depot_serpapi",
            candidates=(),
            warnings=(),
            total_results=0,
            metadata={"cache_hit": self.cache_hit},
        )


def _direct_result(candidates=()):
    return ProviderScanResult(
        provider_key="home_depot_direct",
        candidates=candidates,
        warnings=(),
        total_results=4353,
        metadata={"lane": "direct", "serpapi_credit": 0, "cache_hit": False},
    )


def _request(**metadata):
    return ProviderScanRequest(source_key="home_depot_serpapi", query="drill", metadata=metadata)


def test_composite_direct_success_never_touches_serpapi():
    direct = _FakeDirect(result=_direct_result())
    serpapi = _FakeSerpApi()
    lane = HomeDepotLaneProvider(direct=direct, serpapi=serpapi)
    result = asyncio.run(lane.scan(_request()))
    assert result.metadata["lane"] == "direct"
    assert result.metadata["serpapi_credit"] == 0
    assert serpapi.requests == []


def test_composite_direct_block_falls_back_and_charges_one_credit():
    direct = _FakeDirect(error=HdDirectBlockedError("refused", status=206))
    serpapi = _FakeSerpApi(cache_hit=False)
    lane = HomeDepotLaneProvider(direct=direct, serpapi=serpapi)
    result = asyncio.run(lane.scan(_request()))
    assert result.metadata["lane"] == "serpapi_fallback"
    assert result.metadata["serpapi_credit"] == 1
    assert "HdDirectBlockedError" in result.metadata["fallback_reason"]
    assert len(serpapi.requests) == 1


def test_composite_fallback_cache_hit_charges_zero():
    direct = _FakeDirect(error=HdDirectError("boom"))
    serpapi = _FakeSerpApi(cache_hit=True)
    lane = HomeDepotLaneProvider(direct=direct, serpapi=serpapi)
    result = asyncio.run(lane.scan(_request()))
    assert result.metadata["serpapi_credit"] == 0


def test_composite_quota_blocked_forces_cache_only_fallback():
    direct = _FakeDirect(error=HdDirectBlockedError("refused", status=206))
    serpapi = _FakeSerpApi(cache_hit=False)
    lane = HomeDepotLaneProvider(direct=direct, serpapi=serpapi)
    result = asyncio.run(lane.scan(_request(quota_blocked=True)))
    assert serpapi.requests[0].metadata.get("cache_only") is True
    assert result.metadata["serpapi_credit"] == 0  # cache-only never spends


# ---------------------------------------------------------------------------
# Product detail: direct parser + direct-first fetch
# ---------------------------------------------------------------------------


def test_direct_detail_parser_maps_penny_fields():
    detail = _detail_from_direct_payload("325479354", PRODUCT_PAYLOAD, store_id="6237", zip_code="06484")
    assert detail is not None
    assert detail.price == 0.03
    assert detail.original_price == 159.0
    assert detail.reference_price_source == "direct pricing.original"
    assert detail.title == "M18 18V Compact Drill/Driver Kit"
    assert detail.brand == "Milwaukee"
    assert detail.store_sku_number == "1009516338"
    assert detail.upc == "045242123456"
    assert detail.pickup_quantity == 3
    assert detail.fulfillment_quantity == 3
    assert "<SIZE>" not in (detail.image_url or "")


def test_direct_detail_parser_rejects_priceless_payload():
    payload = {"data": {"product": {"itemId": "1", "identifiers": {}, "pricing": {"value": None}}}}
    assert _detail_from_direct_payload("1", payload) is None


def test_fetch_detail_prefers_direct_and_never_needs_serpapi(monkeypatch):
    async def fake_product_detail(item_id, *, store_id, zip_code):
        return PRODUCT_PAYLOAD

    monkeypatch.setattr(hd_direct_client, "product_detail", fake_product_detail)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    detail = asyncio.run(fetch_home_depot_product_detail("325479354", zip_code="06484", store_id="6237", db=None))
    assert detail is not None
    assert detail.price == 0.03
    assert any("direct gateway" in warning.lower() for warning in detail.warnings)
