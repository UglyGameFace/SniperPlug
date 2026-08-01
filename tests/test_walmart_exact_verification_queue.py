from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    enqueue_walmart_exact_verification_candidates,
    load_recent_verified_queue_candidates,
    process_walmart_exact_verification_queue_batch,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class FakeDetailProvider:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.inner = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        return self.payloads[item_id]


def search_candidate(
    item_id: str,
    *,
    current: float = 20.0,
    reference: float = 100.0,
) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Search item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        image_url=f"https://i5.walmartimages.com/{item_id}.jpg",
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        api_reference_path="search.wasPrice",
        api_discount_percent=round((reference - current) / reference * 100, 2),
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes={
            "finderSourceQuery": "electronics clearance",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": f"{reference:.2f}",
            "trustedReferenceSource": "search.wasPrice",
        },
    )


async def _queue_row_count(conn) -> int:
    cursor = await conn.execute(f"SELECT COUNT(*) FROM {QUEUE_TABLE}")
    row = await cursor.fetchone()
    return int(row[0])


def test_queue_globally_deduplicates_large_search_overflow() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        candidates = [search_candidate(str(100_000 + index)) for index in range(130)]

        first = await enqueue_walmart_exact_verification_candidates(
            db,
            candidates,
            min_discount=50,
            source_label="scheduled:broad",
        )
        second = await enqueue_walmart_exact_verification_candidates(
            db,
            candidates,
            min_discount=50,
            source_label="manual:broad",
        )

        assert first.discovered == 130
        assert first.queued_unique == 130
        assert second.queued_unique == 130
        assert await _queue_row_count(conn) == 130
        cursor = await conn.execute(
            f"SELECT MIN(discovered_count), MAX(discovered_count) FROM {QUEUE_TABLE}"
        )
        counts = await cursor.fetchone()
        assert counts == (2, 2)
        await conn.close()

    asyncio.run(run())


def test_search_candidate_is_not_loaded_until_official_detail_verifies_it() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await enqueue_walmart_exact_verification_candidates(
            db,
            [search_candidate("123456")],
            min_discount=50,
            source_label="scheduled:test",
        )

        assert await load_recent_verified_queue_candidates(db, limit=5) == []
        await conn.close()

    asyncio.run(run())


def test_background_batch_verifies_official_price_and_returns_fresh_snapshot() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await enqueue_walmart_exact_verification_candidates(
            db,
            [search_candidate("654321")],
            min_discount=50,
            source_label="scheduled:test",
        )
        provider = FakeDetailProvider(
            {
                "654321": {
                    "itemId": 654321,
                    "name": "Exact official item",
                    "salePrice": 19.99,
                    "wasPrice": 59.99,
                    "isMarketPlaceItem": False,
                    "availableOnline": True,
                    "largeImage": "https://i5.walmartimages.com/exact.jpg",
                }
            }
        )

        result = await process_walmart_exact_verification_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=50,
        )
        loaded = await load_recent_verified_queue_candidates(db, limit=5)

        assert result.claimed == 1
        assert result.verified == 1
        assert result.official_references == 1
        assert result.markdowns == 1
        assert result.failed == 0
        assert len(loaded) == 1
        exact = loaded[0]
        assert exact.product_id == "654321"
        assert exact.api_current_price == 19.99
        assert exact.api_reference_price == 59.99
        assert exact.seller_name == "Walmart"
        assert exact.variant_attributes["exactDetailPriceProof"] == "yes"
        assert exact.variant_attributes["verificationQueueSource"] == "global_exact_detail_queue"

        cursor = await conn.execute(
            f"SELECT status, exact_current_cents, exact_reference_cents, lease_token FROM {QUEUE_TABLE} WHERE item_id = ?",
            ("654321",),
        )
        row = await cursor.fetchone()
        assert row == ("verified_markdown", 1999, 5999, "")
        await conn.close()

    asyncio.run(run())


def test_background_verified_rows_feed_global_observed_memory() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await enqueue_walmart_exact_verification_candidates(
            db,
            [search_candidate("777777", current=40.0, reference=80.0)],
            min_discount=50,
            source_label="scheduled:test",
        )
        provider = FakeDetailProvider(
            {
                "777777": {
                    "itemId": 777777,
                    "name": "Exact observed-memory item",
                    "salePrice": 40.0,
                    "isMarketPlaceItem": False,
                    "availableOnline": True,
                }
            }
        )

        result = await process_walmart_exact_verification_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=50,
        )
        assert result.verified == 1
        assert result.no_reference == 1

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM walmart_offer_price_memory WHERE item_id = ?",
            ("777777",),
        )
        assert (await cursor.fetchone())[0] == 1
        await conn.close()

    asyncio.run(run())


def test_autoscan_wires_search_overflow_and_worker_without_third_party_api() -> None:
    autoscan = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(
        encoding="utf-8"
    )
    runner = Path("sniperplug/cogs/resilient_auto_scan_runner.py").read_text(
        encoding="utf-8"
    )

    assert "enqueue_walmart_exact_verification_candidates" in autoscan
    assert "load_recent_verified_queue_candidates" in autoscan
    assert "retained in the global exact-detail queue" in autoscan
    assert "process_walmart_exact_verification_queue_batch" in runner
    assert "WALMART_QUEUE_BATCH_SIZE = 6" in runner
    assert "WALMART_QUEUE_INTERVAL_SECONDS = 60" in runner
    assert "RetailerAPI" not in autoscan
    assert "RetailerAPI" not in runner
