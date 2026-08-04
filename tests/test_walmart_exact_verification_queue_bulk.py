from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    QUEUE_UPSERT_CHUNK_SIZE,
    enqueue_walmart_exact_verification_candidates_bulk,
)


class CountingConnection:
    def __init__(self, inner):
        self.inner = inner
        self.queue_upserts = 0

    async def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith(f"INSERT INTO {QUEUE_TABLE}"):
            self.queue_upserts += 1
        return await self.inner.execute(sql, params)

    async def commit(self):
        await self.inner.commit()


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def candidate(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
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


def test_bulk_enqueue_uses_bounded_multirow_statements() -> None:
    async def run() -> None:
        inner = await aiosqlite.connect(":memory:")
        conn = CountingConnection(inner)
        db = FakeDatabase(conn)
        candidates = [candidate(str(900_000 + index)) for index in range(130)]

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            candidates,
            min_discount=50,
            source_label="scheduled:test",
        )

        expected = (130 + QUEUE_UPSERT_CHUNK_SIZE - 1) // QUEUE_UPSERT_CHUNK_SIZE
        assert result.queued_unique == 130
        assert conn.queue_upserts == expected
        cursor = await inner.execute(f"SELECT COUNT(*) FROM {QUEUE_TABLE}")
        assert (await cursor.fetchone())[0] == 130
        await inner.close()

    asyncio.run(run())


def test_global_runtime_uses_request_priority_not_whole_job_provider_lock() -> None:
    global_runner = Path("sniperplug/cogs/global_auto_scan_runner.py").read_text(
        encoding="utf-8"
    )
    registry = Path("sniperplug/providers/registry.py").read_text(
        encoding="utf-8"
    )
    coordinator = Path(
        "sniperplug/services/walmart_request_coordinator.py"
    ).read_text(encoding="utf-8")
    autoscan = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(
        encoding="utf-8"
    )

    assert "_WALMART_PROVIDER_OPERATION_LOCK" not in global_runner
    assert "request_level_provider_priority=true" in global_runner
    assert "catalog_cannot_own_exact_worker=true" in global_runner
    assert "CoordinatedWalmartProvider" in registry
    assert "self._waiting_exact" in coordinator
    assert "exact_has_priority" in coordinator
    assert "enqueue_walmart_exact_verification_candidates_bulk" in autoscan
    assert "foreground_item_ids" in autoscan
    assert "true overflow verified added" in autoscan
