from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_fetch_pacing import (
    PAGE_HTML_PROBE_INTERVAL_SECONDS,
    fetch_exact_candidate_with_page_pacing,
    page_html_probe_remaining_seconds,
    reserve_page_html_probe,
)
from sniperplug.services.walmart_exact_queue_runtime import (
    _page_challenge_circuit_open,
    _retry_kind_from_error,
)
from sniperplug.services.walmart_fresh_discovery_proof import (
    build_fresh_discovery_offer_snapshot,
)


class PageChallengeError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("fixture Walmart human verification page")
        self.retry_kind = "page_challenge"


class PacingInner:
    def __init__(self, *, complete_item_ids: set[str] | None = None) -> None:
        self.builder = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )
        self.complete_item_ids = set(complete_item_ids or ())
        self.official_detail_calls = 0
        self.exact_search_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        self.official_detail_calls += 1
        if item_id in self.complete_item_ids:
            return complete_payload(item_id)
        return incomplete_payload(item_id)

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        self.exact_search_calls += 1
        if item_id in self.complete_item_ids:
            payload = complete_payload(item_id)
        else:
            payload = incomplete_payload(item_id)
        payload["sniperplugOfficialExactSource"] = "affiliate_search_exact_item"
        return payload

    def _candidate_from_item(self, item: dict, request: ProviderScanRequest):
        return self.builder._candidate_from_item(item, request=request)


class PacingProvider:
    def __init__(
        self,
        *,
        complete_item_ids: set[str] | None = None,
        challenge_page: bool = False,
    ) -> None:
        self.inner = PacingInner(complete_item_ids=complete_item_ids)
        self.page_calls = 0
        self.challenge_page = challenge_page

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.page_calls += 1
        if self.challenge_page:
            raise PageChallengeError()
        return complete_payload(item_id)


def incomplete_payload(item_id: str) -> dict:
    return {
        "itemId": int(item_id),
        "name": f"Incomplete API item {item_id}",
        "salePrice": 20.0,
        "wasPrice": 80.0,
        "availableOnline": True,
    }


def complete_payload(item_id: str) -> dict:
    return {
        "itemId": int(item_id),
        "name": f"Complete Walmart item {item_id}",
        "salePrice": 20.0,
        "wasPrice": 80.0,
        "sellerName": "Walmart",
        "isMarketPlaceItem": False,
        "availableOnline": True,
    }


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
        apparent_reference_cents=8000,
        route_hint="",
        lease_token="test",
        attempt_count=0,
        discovery_snapshot_json=discovery_snapshot_json,
        discovery_snapshot_at=discovery_snapshot_at,
    )


def safe_fresh_candidate(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Fresh Walmart item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=20.0,
        typical_price=80.0,
        api_current_price=20.0,
        api_reference_price=80.0,
        api_reference_path="selectedOffer.wasPrice",
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
            "selectedOfferReferencePrice": "80.00",
            "selectedOfferReferenceSource": "selectedOffer.wasPrice",
            "selectedOfferReferenceTrusted": "yes",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "80.00",
            "trustedReferenceSource": "selectedOffer.wasPrice",
        },
    )


def test_page_probe_reservation_is_single_slot_per_minute() -> None:
    provider = PacingProvider()

    assert PAGE_HTML_PROBE_INTERVAL_SECONDS == 60.0
    assert reserve_page_html_probe(provider, now_monotonic=100.0) is True
    assert reserve_page_html_probe(provider, now_monotonic=110.0) is False
    assert page_html_probe_remaining_seconds(provider, now_monotonic=110.0) == 50
    assert reserve_page_html_probe(provider, now_monotonic=160.0) is True


def test_concurrent_incomplete_claims_issue_only_one_html_fallback() -> None:
    async def run() -> None:
        provider = PacingProvider()
        outcomes = await asyncio.gather(
            *(
                fetch_exact_candidate_with_page_pacing(
                    provider,
                    queue_claim(str(810001 + index)),
                    timeout_seconds=5.0,
                )
                for index in range(4)
            )
        )

        assert provider.page_calls == 1
        assert sum(1 for candidate, status, error in outcomes if candidate is not None and status == "verified" and not error) == 1
        deferred = [
            _retry_kind_from_error(error)
            for candidate, status, error in outcomes
            if candidate is None
        ]
        assert deferred.count("page_pacing_deferred") == 3
        assert provider.inner.official_detail_calls == 4
        assert provider.inner.exact_search_calls == 4

    asyncio.run(run())


def test_complete_official_api_still_verifies_while_html_probe_is_throttled() -> None:
    async def run() -> None:
        item_id = "820001"
        provider = PacingProvider(complete_item_ids={item_id})
        assert reserve_page_html_probe(provider, now_monotonic=100.0) is True

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim(item_id),
            timeout_seconds=5.0,
        )

        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.seller_name == "Walmart"
        assert provider.page_calls == 0
        assert provider.inner.official_detail_calls == 1

    asyncio.run(run())


def test_first_page_challenge_opens_circuit_immediately_and_blocks_more_html() -> None:
    async def run() -> None:
        provider = PacingProvider(challenge_page=True)

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim("830001"),
            timeout_seconds=5.0,
        )
        assert candidate is None
        assert status == "page_retry"
        assert _retry_kind_from_error(error) == "page_challenge"
        assert provider.page_calls == 1
        assert _page_challenge_circuit_open(provider) is True

        candidate, status, error = await fetch_exact_candidate_with_page_pacing(
            provider,
            queue_claim("830002"),
            timeout_seconds=5.0,
        )
        assert candidate is None
        assert status == "page_retry"
        assert _retry_kind_from_error(error) == "page_circuit_open"
        assert provider.page_calls == 1
        assert provider.inner.official_detail_calls == 2
        assert provider.inner.exact_search_calls == 2

    asyncio.run(run())


def test_fresh_catalog_proof_bypasses_api_and_html_entirely() -> None:
    async def run() -> None:
        item_id = "840001"
        provider = PacingProvider()
        fresh = safe_fresh_candidate(item_id)
        snapshot = build_fresh_discovery_offer_snapshot(fresh)
        assert snapshot is not None

        # The proof builder stores its own observation timestamp in the payload,
        # but queue promotion requires the queue column timestamp as well.
        from datetime import datetime, timezone

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
        assert provider.inner.official_detail_calls == 0
        assert provider.inner.exact_search_calls == 0
        assert provider.page_calls == 0

    asyncio.run(run())
