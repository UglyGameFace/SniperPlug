from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS,
    QUEUE_UPSERT_CHUNK_SIZE,
    enqueue_walmart_exact_verification_candidates_bulk,
)


class CountingConnection:
    def __init__(self, inner):
        self.inner = inner
        self.queue_upserts = 0
        self.commits = 0

    async def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith(f"INSERT INTO {QUEUE_TABLE}"):
            self.queue_upserts += 1
        return await self.inner.execute(sql, params)

    async def commit(self):
        self.commits += 1
        await self.inner.commit()


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def candidate(
    item_id: str,
    *,
    current: float = 20.0,
    reference: float = 100.0,
    title: str | None = None,
    route: str = "electronics clearance",
) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=title or f"Item {item_id}",
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
        variant_attributes={"finderSourceQuery": route},
    )


async def _new_db():
    inner = await aiosqlite.connect(":memory:")
    conn = CountingConnection(inner)
    return inner, conn, FakeDatabase(conn)


def test_recent_identical_catalog_rediscovery_skips_all_queue_writes() -> None:
    async def run() -> None:
        inner, conn, db = await _new_db()
        candidates = [candidate(str(700_000 + index)) for index in range(75)]

        first = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            candidates,
            min_discount=50,
            source_label="global_catalog_autoscan:global_catalog_autoscan",
        )
        assert first.persisted_rows == 75
        assert first.unchanged_rows == 0
        assert first.write_statements == 2

        conn.queue_upserts = 0
        conn.commits = 0
        second = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            candidates,
            min_discount=50,
            source_label="global_catalog_autoscan:global_catalog_autoscan",
        )

        assert second.queued_unique == 75
        assert second.persisted_rows == 0
        assert second.unchanged_rows == 75
        assert second.write_statements == 0
        assert conn.queue_upserts == 0
        assert conn.commits == 0
        cursor = await inner.execute(
            f"SELECT MIN(discovered_count), MAX(discovered_count) FROM {QUEUE_TABLE}"
        )
        assert await cursor.fetchone() == (1, 1)
        await inner.close()

    asyncio.run(run())


def test_rotating_nonempty_catalog_route_does_not_force_a_write() -> None:
    async def run() -> None:
        inner, conn, db = await _new_db()
        source = "global_catalog_autoscan:global_catalog_autoscan"
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("810000", route="electronics clearance")],
            min_discount=50,
            source_label=source,
        )

        conn.queue_upserts = 0
        conn.commits = 0
        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("810000", route="school backpacks")],
            min_discount=50,
            source_label=source,
        )

        assert result.persisted_rows == 0
        assert result.unchanged_rows == 1
        assert result.write_statements == 0
        assert conn.queue_upserts == 0
        assert conn.commits == 0
        cursor = await inner.execute(
            f"SELECT route_hint FROM {QUEUE_TABLE} WHERE item_id = ?",
            ("810000",),
        )
        assert (await cursor.fetchone())[0] == "electronics clearance"
        await inner.close()

    asyncio.run(run())


def test_stale_discovery_heartbeat_is_persisted() -> None:
    async def run() -> None:
        inner, conn, db = await _new_db()
        item = candidate("810001")
        source = "global_catalog_autoscan:global_catalog_autoscan"
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [item],
            min_discount=50,
            source_label=source,
        )
        stale = (
            datetime.now(timezone.utc)
            - timedelta(seconds=QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS + 60)
        ).isoformat()
        await inner.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET status = 'verified_markdown', last_seen_at = ?
            WHERE item_id = ?
            """,
            (stale, "810001"),
        )
        await inner.commit()

        conn.queue_upserts = 0
        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [item],
            min_discount=50,
            source_label=source,
        )

        assert result.persisted_rows == 1
        assert result.unchanged_rows == 0
        assert result.write_statements == 1
        assert conn.queue_upserts == 1
        cursor = await inner.execute(
            f"""
            SELECT discovered_count, last_seen_at
            FROM {QUEUE_TABLE}
            WHERE item_id = ?
            """,
            ("810001",),
        )
        discovered_count, last_seen_at = await cursor.fetchone()
        assert discovered_count == 2
        assert last_seen_at > stale
        await inner.close()

    asyncio.run(run())


def test_retry_row_is_rearmed_even_when_discovery_is_recent() -> None:
    async def run() -> None:
        inner, _conn, db = await _new_db()
        item = candidate("810002")
        source = "global_catalog_autoscan:global_catalog_autoscan"
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [item],
            min_discount=50,
            source_label=source,
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        await inner.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET status = 'retry', next_attempt_at = ?
            WHERE item_id = ?
            """,
            (future, "810002"),
        )
        await inner.commit()

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [item],
            min_discount=50,
            source_label=source,
        )

        assert result.persisted_rows == 1
        cursor = await inner.execute(
            f"""
            SELECT discovered_count, next_attempt_at
            FROM {QUEUE_TABLE}
            WHERE item_id = ?
            """,
            ("810002",),
        )
        discovered_count, next_attempt_at = await cursor.fetchone()
        assert discovered_count == 2
        assert next_attempt_at < future
        await inner.close()

    asyncio.run(run())


def test_changed_price_title_and_source_are_never_suppressed() -> None:
    async def run() -> None:
        inner, _conn, db = await _new_db()
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("810003")],
            min_discount=50,
            source_label="scheduled:test",
        )

        changed = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("810003", current=15.0, title="Changed item")],
            min_discount=50,
            source_label="scheduled:test",
        )
        assert changed.persisted_rows == 1

        source_changed = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("810003", current=15.0, title="Changed item")],
            min_discount=50,
            source_label="manual:test",
        )
        assert source_changed.persisted_rows == 1

        cursor = await inner.execute(
            f"""
            SELECT apparent_current_cents, title, source_label, discovered_count
            FROM {QUEUE_TABLE}
            WHERE item_id = ?
            """,
            ("810003",),
        )
        row = await cursor.fetchone()
        assert row == (1500, "Changed item", "manual:test", 3)
        await inner.close()

    asyncio.run(run())


def test_catalog_enqueue_uses_cached_schema_and_safe_larger_batches() -> None:
    source = Path(
        "sniperplug/services/walmart_exact_verification_queue_bulk.py"
    ).read_text(encoding="utf-8")

    assert "ensure_exact_runtime_schema_once" in source
    assert "await ensure_walmart_exact_verification_queue(db)" not in source
    assert QUEUE_UPSERT_CHUNK_SIZE == 60
    assert QUEUE_UPSERT_CHUNK_SIZE * 16 <= 999
