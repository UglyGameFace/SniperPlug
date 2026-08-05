from __future__ import annotations

import asyncio

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
)


class RecordingConnection:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.full_queue_counts = 0
        self.bounded_pressure_reads = 0

    async def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith(f"SELECT COUNT(*) FROM {QUEUE_TABLE}"):
            self.full_queue_counts += 1
        if normalized.startswith("WITH fresh_due AS ("):
            self.bounded_pressure_reads += 1
        return await self.inner.execute(sql, params)

    async def commit(self) -> None:
        await self.inner.commit()


class FakeDatabase:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def candidate(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=20.0,
        typical_price=100.0,
        api_current_price=20.0,
        api_reference_price=100.0,
        api_reference_path="search.wasPrice",
        api_discount_percent=80.0,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes={"finderSourceQuery": "electronics clearance"},
    )


def test_catalog_enqueue_uses_bounded_pressure_not_full_queue_count() -> None:
    async def run() -> None:
        inner = await aiosqlite.connect(":memory:")
        conn = RecordingConnection(inner)
        db = FakeDatabase(conn)

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("900001")],
            min_discount=50,
            source_label="scheduled:test",
        )

        assert result.pending_total == 1
        assert conn.full_queue_counts == 0
        assert conn.bounded_pressure_reads == 1
        await inner.close()

    asyncio.run(run())
