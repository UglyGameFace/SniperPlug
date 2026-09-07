from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_fetch_pacing import (
    fetch_exact_candidate_with_page_pacing,
)
from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference
from sniperplug.services.walmart_exact_queue_runtime import _PAGE_CIRCUIT_STATE_ATTR
from sniperplug.services.walmart_fresh_discovery_proof import (
    build_fresh_discovery_offer_snapshot,
)


class RecoveryInner:
    def __init__(
        self,
        *,
        detail_payload: dict,
        search_payload: dict | None,
        search_error: Exception | None = None,
    ) -> None:
        self.builder = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )
        self.detail_payload = dict(detail_payload)
        self.search_payload = dict(search_payload) if search_payload is not None else None
        self.search_error = search_error
        self.detail_calls = 0
        self.search_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        self.detail_calls += 1
        assert str(self.detail_payload.get("itemId")) == str(item_id)
        return dict(self.detail_payload)

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        self.search_calls += 1
        if self.search_error is not None:
            raise self.search_error
        assert self.search_payload is not None
        assert str(self.search_payload.get("itemId")) == str(item_id)
        payload = dict(self.search_payload)
        payload["sniperplugOfficialExactSource"] = "affiliate_search_exact_item"
        return payload

    def _candidate_from_item(self, item: dict, request: ProviderScanRequest):
        return self.builder._candidate_from_item(item, request=request)


class RecoveryProvider:
    def __init__(self, inner: RecoveryInner) -> None:
        self.inner = inner
        self.page_calls = 0

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.page_calls += 1
        raise AssertionError(f"HTML/page recovery must not run for {item_id}")


def queue_claim(
    item_id: str,
    *,
    discovery_snapshot_json: str = "",
    discovery_snapshot_at: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        item_id=item_id,
        title=f"Queue item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        image_url="",
        apparent_current_cents=2000,
        apparent_reference_cents=None,
        route_hint="",
        lease_token="test",
        attempt_count=0,
        discovery_snapshot_json=discovery_snapshot_json,
        discovery_snapshot_at=discovery_snapshot_at,
    )


def detail_identity_without_price(item_id: str) -> dict:
    return {
        "itemId": int(item_id),
        "name": f"Identity-only Walmart item {item_id}",
        "sellerName": "Walmart",
        "isMarketPlaceItem": False,
        "availableOnline": True,
    }


def exact_search_with_price_and_reference(item_id: str) -> dict:
    return {
        "itemId": int(item_id),
        "name": f"Exact search Walmart item {item_id}",
        "salePrice": 20.0,
        "wasPrice": 80.0,
        "sellerName": "Walmart",
        "isMarketPlaceItem": False,
        "availableOnline": True,
    }


def fresh_without_reference(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Fresh Walmart item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=20.0,
        typical_price=None,
        api_current_price=20.0,
        api_reference_price=None,
        api_price_path="selectedOffer.salePrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=f"offer-{item_id}",
        seller_name="Walmart",
        condition="new",
        stock_status="in stock",
        can_add_to_cart=True,
        variant_attributes={
            "selectedOfferNodeSource": "selectedOffer",
            "selectedOfferSeller": "Walmart",
            "selectedOfferId": f"offer-{item_id}",
            "selectedOfferCondition": "new",
            "selectedOfferMarketplace": "no",
            "selectedOfferItemPrice": "20.00",
            "selectedOfferItemPriceSource": "selectedOffer.salePrice",
            "referencePriceTrusted": "no",
        },
    )


def test_identity_only_detail_recovers_current_and_reference_from_exact_search() -> None:
    async def run() -> None:
        item_id = "910101"
        inner = RecoveryInner(
            detail_payload=detail_identity_without_price(item_id),
            search_payload=exact_search_with_price_and_reference(item_id),
        )
        provider = RecoveryProvider(inner)

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim(item_id),
            timeout_seconds=5.0,
        )

        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.current_price == 20.0
        assert candidate.api_current_price == 20.0
        assert _trusted_reference(candidate) == 80.0
        assert candidate.seller_name == "Walmart"
        assert inner.detail_calls == 1
        assert inner.search_calls == 1
        assert provider.page_calls == 0

    asyncio.run(run())


def test_price_recovery_still_runs_while_real_html_circuit_is_open() -> None:
    async def run() -> None:
        item_id = "910104"
        inner = RecoveryInner(
            detail_payload=detail_identity_without_price(item_id),
            search_payload=exact_search_with_price_and_reference(item_id),
        )
        setattr(
            inner,
            _PAGE_CIRCUIT_STATE_ATTR,
            {"events": [], "until": time.monotonic() + 300.0},
        )
        provider = RecoveryProvider(inner)

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim(item_id),
            timeout_seconds=5.0,
        )

        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.current_price == 20.0
        assert _trusted_reference(candidate) == 80.0
        assert inner.detail_calls == 1
        assert inner.search_calls == 1
        assert provider.page_calls == 0

    asyncio.run(run())


def test_fresh_no_reference_gets_best_effort_exact_search_reference_without_html() -> None:
    async def run() -> None:
        item_id = "910102"
        fresh = fresh_without_reference(item_id)
        snapshot = build_fresh_discovery_offer_snapshot(fresh)
        assert snapshot

        inner = RecoveryInner(
            detail_payload=detail_identity_without_price(item_id),
            search_payload=exact_search_with_price_and_reference(item_id),
        )
        provider = RecoveryProvider(inner)

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim(
                item_id,
                discovery_snapshot_json=snapshot,
                discovery_snapshot_at=datetime.now(timezone.utc).isoformat(),
            ),
            timeout_seconds=5.0,
        )

        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.current_price == 20.0
        assert _trusted_reference(candidate) == 80.0
        # The first canonical pass used the fresh snapshot, then one exact-search
        # recovery pass supplied the trusted reference. No ordinary detail or HTML.
        assert inner.detail_calls == 0
        assert inner.search_calls == 1
        assert provider.page_calls == 0

    asyncio.run(run())


def test_failed_reference_recovery_preserves_valid_fresh_candidate() -> None:
    async def run() -> None:
        item_id = "910103"
        fresh = fresh_without_reference(item_id)
        snapshot = build_fresh_discovery_offer_snapshot(fresh)
        assert snapshot

        inner = RecoveryInner(
            detail_payload=detail_identity_without_price(item_id),
            search_payload=None,
            search_error=RuntimeError("fixture exact search unavailable"),
        )
        provider = RecoveryProvider(inner)

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim(
                item_id,
                discovery_snapshot_json=snapshot,
                discovery_snapshot_at=datetime.now(timezone.utc).isoformat(),
            ),
            timeout_seconds=5.0,
        )

        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.current_price == 20.0
        assert _trusted_reference(candidate) is None
        attrs = dict(candidate.variant_attributes or {})
        assert attrs.get("exactDetailPriceProofSource") == "affiliate_catalog_fresh_snapshot"
        assert inner.detail_calls == 0
        assert inner.search_calls == 1
        assert provider.page_calls == 0

    asyncio.run(run())
