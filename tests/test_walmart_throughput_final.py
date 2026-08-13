from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_queue_drain import (
    PAGE_RECOVERY_CLAIM_CAP,
    claim_due_rows_batched,
)
from sniperplug.services.walmart_exact_queue_runtime import (
    PAGE_CHALLENGE_CIRCUIT_THRESHOLD,
    _fetch_exact_candidate_off_loop,
    _page_challenge_circuit_open,
    _retry_kind_from_error,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
)
from sniperplug.services.walmart_fresh_discovery_proof import (
    build_fresh_discovery_offer_snapshot,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def safe_walmart_candidate(item_id: str, *, current: float = 39.99) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Safe fresh Walmart item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=99.99,
        api_current_price=current,
        api_reference_price=99.99,
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
            "selectedOfferItemPrice": f"{current:.2f}",
            "selectedOfferItemPriceSource": "selectedOffer.salePrice",
            "selectedOfferReferencePrice": "99.99",
            "selectedOfferReferenceSource": "selectedOffer.wasPrice",
            "selectedOfferReferenceTrusted": "yes",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "99.99",
            "trustedReferenceSource": "selectedOffer.wasPrice",
        },
    )


def test_claims_fresh_catalog_proof_before_stale_page_backlog_and_caps_page_lane() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        due = (now - timedelta(minutes=1)).isoformat()

        fresh_candidate = safe_walmart_candidate("700004")
        fresh_snapshot = build_fresh_discovery_offer_snapshot(fresh_candidate)
        assert fresh_snapshot

        rows = (
            ("700001", "page_retry", 10000, "", None),
            ("700002", "page_retry", 9000, "", None),
            ("700003", "page_retry", 8000, "", None),
            # Lower priority than every stale page row: freshness must still win.
            ("700004", "page_retry", 1, fresh_snapshot, now.isoformat()),
            # Scheduled verified work must also not sit behind unlimited page retries.
            ("700005", "verified_markdown", 500, "", None),
        )
        for item_id, status, priority, snapshot, snapshot_at in rows:
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, priority_score, apparent_current_cents,
                    apparent_reference_cents, apparent_discount_bps,
                    title, product_url, first_seen_at, last_seen_at,
                    status, next_attempt_at, discovery_snapshot_json,
                    discovery_snapshot_at
                ) VALUES (?, ?, 3999, 9999, 6000, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    priority,
                    f"Item {item_id}",
                    f"https://www.walmart.com/ip/{item_id}",
                    now.isoformat(),
                    now.isoformat(),
                    status,
                    due,
                    snapshot,
                    snapshot_at,
                ),
            )
        await conn.commit()

        claims = await claim_due_rows_batched(conn, now=now, limit=8)
        claimed_ids = [claim.item_id for claim in claims]

        assert claimed_ids[0] == "700004"
        assert "700005" in claimed_ids
        stale_page_claims = [
            item_id for item_id in claimed_ids if item_id in {"700001", "700002", "700003"}
        ]
        assert len(stale_page_claims) == PAGE_RECOVERY_CLAIM_CAP
        assert len(claimed_ids) == 2 + PAGE_RECOVERY_CLAIM_CAP
        await conn.close()

    asyncio.run(run())


def test_safe_catalog_snapshot_rearms_page_retry_as_pending_and_reports_rescue() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        item_id = "710001"
        await conn.execute(
            f"""
            INSERT INTO {QUEUE_TABLE} (
                item_id, priority_score, apparent_current_cents,
                apparent_reference_cents, apparent_discount_bps,
                title, product_url, source_label,
                first_seen_at, last_seen_at, status, next_attempt_at
            ) VALUES (?, 1, 3999, 9999, 6000, ?, ?, 'old', ?, ?, 'page_retry', ?)
            """,
            (
                item_id,
                "Old challenged page row",
                f"https://www.walmart.com/ip/{item_id}",
                now.isoformat(),
                now.isoformat(),
                (now + timedelta(hours=1)).isoformat(),
            ),
        )
        await conn.commit()

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [safe_walmart_candidate(item_id)],
            min_discount=40,
            source_label="global_catalog_autoscan:test",
        )

        cursor = await conn.execute(
            f"""
            SELECT status, next_attempt_at, discovery_snapshot_json
            FROM {QUEUE_TABLE}
            WHERE item_id = ?
            """,
            (item_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["discovery_snapshot_json"]
        assert datetime.fromisoformat(row["next_attempt_at"]) <= datetime.now(timezone.utc)
        assert getattr(result, "safe_snapshot_rows", 0) == 1
        assert getattr(result, "page_retry_rearmed", 0) == 1
        assert "safe fresh proof **1**" in result.summary_line()
        assert "page retries rescued **1**" in result.summary_line()
        await conn.close()

    asyncio.run(run())


class PageChallengeError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("fixture Walmart human verification page")
        self.retry_kind = "page_challenge"


class CircuitInner:
    def __init__(self) -> None:
        self.builder = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )
        self.official_detail_calls = 0
        self.exact_search_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        self.official_detail_calls += 1
        if item_id == "720004":
            return {
                "itemId": int(item_id),
                "name": "API survives open page circuit",
                "salePrice": 20.0,
                "wasPrice": 80.0,
                "sellerName": "Walmart",
                "isMarketPlaceItem": False,
                "availableOnline": True,
            }
        return {
            "itemId": int(item_id),
            "name": f"Incomplete API item {item_id}",
            "salePrice": 20.0,
            "wasPrice": 80.0,
            "availableOnline": True,
        }

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        self.exact_search_calls += 1
        return {
            "itemId": int(item_id),
            "name": f"Still incomplete exact search {item_id}",
            "salePrice": 20.0,
            "wasPrice": 80.0,
            "availableOnline": True,
            "sniperplugOfficialExactSource": "affiliate_search_exact_item",
        }

    def _candidate_from_item(self, item: dict, request: ProviderScanRequest):
        return self.builder._candidate_from_item(item, request=request)


class CircuitProvider:
    def __init__(self) -> None:
        self.inner = CircuitInner()
        self.page_calls = 0

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.page_calls += 1
        raise PageChallengeError()


def queue_claim(item_id: str) -> SimpleNamespace:
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
        discovery_snapshot_json="",
        discovery_snapshot_at=None,
    )


def test_page_challenge_circuit_opens_after_two_failures_then_suppresses_html_but_keeps_api() -> None:
    async def run() -> None:
        provider = CircuitProvider()

        for index in range(PAGE_CHALLENGE_CIRCUIT_THRESHOLD):
            candidate, status, error = await _fetch_exact_candidate_off_loop(
                provider,
                queue_claim(str(720001 + index)),
                timeout_seconds=5.0,
            )
            assert candidate is None
            assert status == "page_retry"
            assert _retry_kind_from_error(error) == "page_challenge"

        assert provider.page_calls == PAGE_CHALLENGE_CIRCUIT_THRESHOLD
        assert _page_challenge_circuit_open(provider) is True

        # The next incomplete item must not issue another product-page request.
        candidate, status, error = await _fetch_exact_candidate_off_loop(
            provider,
            queue_claim("720003"),
            timeout_seconds=5.0,
        )
        assert candidate is None
        assert status == "page_retry"
        assert _retry_kind_from_error(error) == "page_circuit_open"
        assert provider.page_calls == PAGE_CHALLENGE_CIRCUIT_THRESHOLD
        assert provider.inner.official_detail_calls >= 1
        assert provider.inner.exact_search_calls >= 1

        # A complete official API response must still verify while HTML is held.
        candidate, status, error = await _fetch_exact_candidate_off_loop(
            provider,
            queue_claim("720004"),
            timeout_seconds=5.0,
        )
        assert status == "verified"
        assert error == ""
        assert candidate is not None
        assert candidate.seller_name == "Walmart"
        assert provider.page_calls == PAGE_CHALLENGE_CIRCUIT_THRESHOLD

    asyncio.run(run())
