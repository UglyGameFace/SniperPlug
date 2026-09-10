from __future__ import annotations

import asyncio
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_fetch_pacing import (
    fetch_exact_candidate_with_page_pacing,
)


class IncompleteIdentityRecoveryInner:
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
        self.detail_calls = 0
        self.lookup_urls: list[str] = []
        self.legacy_search_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        self.detail_calls += 1
        assert item_id == self.item_id
        return {
            "itemId": int(item_id),
            "name": "Identity-only primary detail",
            "sellerName": "Walmart",
            "isMarketPlaceItem": False,
            "availableOnline": True,
        }

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        self.legacy_search_calls += 1
        raise AssertionError(
            f"Product Lookup recovery must not hand {item_id} to relevance Search"
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
                    # Deliberately incomplete selected-offer identity. The exact
                    # lookup price must not be replaced by a relevance Search row
                    # merely to obtain seller identity.
                    "isMarketPlaceItem": True,
                    "availableOnline": True,
                }
            ]
        }

    def _candidate_from_item(self, item: dict, request: ProviderScanRequest):
        return self.builder._candidate_from_item(item, request=request)


class RecoveryProvider:
    def __init__(self, inner: IncompleteIdentityRecoveryInner) -> None:
        self.inner = inner
        self.page_calls = 0

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.page_calls += 1
        raise AssertionError(f"HTML/page recovery must not run for {item_id}")


def _claim(item_id: str) -> SimpleNamespace:
    return SimpleNamespace(
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


def test_product_lookup_price_with_incomplete_identity_never_hands_off_to_search() -> None:
    async def run() -> None:
        item_id = "910276"
        inner = IncompleteIdentityRecoveryInner(item_id)
        provider = RecoveryProvider(inner)

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            _claim(item_id),
            timeout_seconds=5.0,
        )

        assert candidate is None
        assert status == "retry"
        assert "retry_kind=api_price_missing" in error
        assert "recovery_kind=api_exact_lookup_identity_incomplete" in error
        assert inner.detail_calls == 1
        assert len(inner.lookup_urls) == 1
        assert inner.legacy_search_calls == 0
        assert provider.page_calls == 0

    asyncio.run(run())


def test_bulk_runtime_marks_no_search_product_lookup_handoff_fix() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "sniperplug/services/walmart_exact_queue_bulk_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "exact_lookup=affiliate_items_ids_v276" in runtime
