from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services import walmart_exact_verification_queue as queue


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_recent_worker_verification_does_not_keep_undiscovered_row_alive(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await queue.ensure_walmart_exact_verification_queue(db)
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        old_discovery = (now - timedelta(days=queue.QUEUE_RETENTION_DAYS + 1)).isoformat()
        recent_verification = (now - timedelta(minutes=5)).isoformat()
        await conn.execute(
            f"""
            INSERT INTO {queue.QUEUE_TABLE} (
                item_id, first_seen_at, last_seen_at, status,
                next_attempt_at, verified_at
            ) VALUES (?, ?, ?, 'verified_markdown', ?, ?)
            """,
            (
                "123456",
                old_discovery,
                old_discovery,
                now.isoformat(),
                recent_verification,
            ),
        )
        await conn.commit()

        monkeypatch.setattr(queue, "_last_cleanup_monotonic", 0.0)
        monkeypatch.setattr(
            queue.time,
            "monotonic",
            lambda: queue.QUEUE_CLEANUP_INTERVAL_SECONDS + 10.0,
        )
        await queue.maybe_prune_walmart_exact_verification_queue(conn, now=now)

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {queue.QUEUE_TABLE} WHERE item_id = ?",
            ("123456",),
        )
        assert (await cursor.fetchone())[0] == 0
        await conn.close()

    asyncio.run(run())


def test_stale_rows_are_not_claimed_even_when_cleanup_is_throttled() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await queue.ensure_walmart_exact_verification_queue(db)
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        old_discovery = (now - timedelta(days=queue.QUEUE_RETENTION_DAYS + 1)).isoformat()
        await conn.execute(
            f"""
            INSERT INTO {queue.QUEUE_TABLE} (
                item_id, first_seen_at, last_seen_at, status, next_attempt_at
            ) VALUES (?, ?, ?, 'pending', ?)
            """,
            ("654321", old_discovery, old_discovery, old_discovery),
        )
        await conn.commit()

        claims = await queue._claim_due_rows(conn, now=now, limit=10)
        assert claims == []
        assert await queue._pending_total(conn, now_iso=now.isoformat()) == 0
        await conn.close()

    asyncio.run(run())


def test_queue_diagnostics_do_not_inflate_remembered_rechecks() -> None:
    source = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(
        encoding="utf-8"
    )

    assert "memory_recheck_count=len(memory_seeds)" in source
    assert "memory_recheck_count=len(memory_seeds) +" not in source
    assert "surfaced_current_search_ids" in source
    assert "search_item_ids - surfaced_current_search_ids" in source
    assert "true overflow verified added" in source
