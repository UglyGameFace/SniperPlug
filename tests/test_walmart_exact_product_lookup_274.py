from __future__ import annotations

import asyncio
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import (
    WalmartAffiliateConfig,
    WalmartProvider,
    WalmartProviderError,
)
from sniperplug.providers.walmart_exact_lookup import (
    AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE,
    AFFILIATE_PRODUCT_LOOKUP_ROUTE,
    _fetch_exact_affiliate_product_lookup_payload_sync,
    exact_item_from_product_lookup_payload,
)
from sniperplug.services.walmart_exact_fetch_pacing import (
    fetch_exact_candidate_with_page_pacing,
)
from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference


class ProductLookupFixture:
    detail_url = WalmartProvider.detail_url

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.urls: list[str] = []
        self.config = SimpleNamespace(publisher_id=None)

    def _request_json(self, url: str) -> dict:
        self.urls.append(url)
        return self.payload


def test_product_lookup_uses_ids_not_free_text_query_and_marks_exact_source() -> None:
    fixture = ProductLookupFixture(
        {
            "items": [
                {
                    "itemId": 910201,
                    "name": "Exact lookup item",
                    "salePrice": 20.0,
                    "sellerName": "Walmart",
                }
            ]
        }
    )

    payload = _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910201")

    assert len(fixture.urls) == 1
    parsed = urllib.parse.urlparse(fixture.urls[0])
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.path.endswith("/affil/product/v2/items")
    assert query["ids"] == ["910201"]
    assert query["responseGroup"] == ["full"]
    assert "query" not in query
    assert payload["itemId"] == 910201
    assert payload["sniperplugOfficialExactSource"] == AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE
    assert payload["sniperplugOfficialExactRoute"] == AFFILIATE_PRODUCT_LOOKUP_ROUTE


def test_product_lookup_rejects_nonmatching_neighbor_even_when_response_has_items() -> None:
    fixture = ProductLookupFixture(
        {
            "items": [
                {
                    "itemId": 999999,
                    "name": "Wrong item",
                    "salePrice": 1.0,
                }
            ]
        }
    )

    try:
        _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910202")
    except WalmartProviderError as exc:
        assert exc.retry_kind == "api_exact_lookup_no_match"
    else:
        raise AssertionError("Product Lookup must never accept another Walmart item ID")


def test_exact_item_parser_accepts_known_wrappers_only_with_exact_item_id() -> None:
    payload = {
        "data": {
            "itemResponse": [
                {"itemId": 111, "name": "wrong"},
                {"usItemId": "910203", "name": "right", "salePrice": 12.5},
            ]
        }
    }

    exact = exact_item_from_product_lookup_payload(payload, "910203")

    assert exact is not None
    assert exact["name"] == "right"
    assert exact_item_from_product_lookup_payload(payload, "910204") is None


class RecoveryInner:
    detail_url = WalmartProvider.detail_url

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        self.builder = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )
        self.config = self.builder.config
        self.lookup_urls: list[str] = []
        self.detail_calls = 0
        self.legacy_search_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        self.detail_calls += 1
        assert item_id == self.item_id
        return {
            "itemId": int(item_id),
            "name": "Identity-only detail",
            "sellerName": "Walmart",
            "isMarketPlaceItem": False,
            "availableOnline": True,
        }

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        self.legacy_search_calls += 1
        raise AssertionError(
            f"free-text Affiliate search must not run for production exact lookup {item_id}"
        )

    def _request_json(self, url: str) -> dict:
        self.lookup_urls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert query.get("ids") == [self.item_id]
        assert "query" not in query
        return {
            "items": [
                {
                    "itemId": int(self.item_id),
                    "name": "Exact Product Lookup item",
                    "salePrice": 20.0,
                    "wasPrice": 80.0,
                    "sellerName": "Walmart",
                    "isMarketPlaceItem": False,
                    "availableOnline": True,
                }
            ]
        }

    def _candidate_from_item(self, item: dict, request: ProviderScanRequest):
        return self.builder._candidate_from_item(item, request=request)


class RecoveryProvider:
    def __init__(self, inner: RecoveryInner) -> None:
        self.inner = inner
        self.page_calls = 0

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.page_calls += 1
        raise AssertionError(f"HTML/page recovery must not run for {item_id}")


def test_api_price_missing_recovers_through_product_lookup_not_search_or_html() -> None:
    async def run() -> None:
        item_id = "910205"
        inner = RecoveryInner(item_id)
        provider = RecoveryProvider(inner)
        claim = SimpleNamespace(
            item_id=item_id,
            title="Queue item",
            product_url=f"https://www.walmart.com/ip/{item_id}",
            image_url="",
            apparent_current_cents=2000,
            apparent_reference_cents=None,
            route_hint="",
            lease_token="test",
            attempt_count=0,
            discovery_snapshot_json="",
            discovery_snapshot_at=None,
        )

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            claim,
            timeout_seconds=5.0,
        )

        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.current_price == 20.0
        assert candidate.api_current_price == 20.0
        assert _trusted_reference(candidate) == 80.0
        assert inner.detail_calls == 1
        assert len(inner.lookup_urls) == 1
        assert inner.legacy_search_calls == 0
        assert provider.page_calls == 0

    asyncio.run(run())


def test_bulk_runtime_marks_product_lookup_recovery_capability() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "sniperplug/services/walmart_exact_queue_bulk_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "exact_lookup=affiliate_items_ids_v276" in runtime
