from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services.walmart_exact_queue_drain import claim_due_rows_batched
from sniperplug.services.walmart_exact_queue_health import load_walmart_exact_queue_health
from sniperplug.services.walmart_exact_queue_pressure import load_walmart_exact_queue_pressure
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_legacy_early_verified_recheck_is_ignored_without_repair_write() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)

        now = datetime.now(timezone.utc)
        due = (now - timedelta(minutes=5)).isoformat()
        last_seen = now.isoformat()
        rows = (
            (
                "recent-verified",
                "verified_markdown",
                (now - timedelta(hours=4)).isoformat(),
            ),
            (
                "old-verified",
                "verified_markdown",
                (now - timedelta(hours=25)).isoformat(),
            ),
            (
                "fresh-pending",
                "pending",
                None,
            ),
        )
        for item_id, status, verified_at in rows:
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, priority_score, first_seen_at, last_seen_at,
                    status, verified_at, last_attempt_at, next_attempt_at
                ) VALUES (?, 100, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    (now - timedelta(days=2)).isoformat(),
                    last_seen,
                    status,
                    verified_at,
                    verified_at,
                    due,
                ),
            )
        await conn.commit()

        pressure = await load_walmart_exact_queue_pressure(db, cap=64)
        assert pressure.initial_due_now == 1
        assert pressure.recheck_due_now == 1
        assert pressure.due_now == 2

        health = await load_walmart_exact_queue_health(db)
        assert health.initial_due_now == 1
        assert health.recheck_due_now == 1
        assert health.due_now == 2

        claims = await claim_due_rows_batched(conn, now=now, limit=10)
        assert [claim.item_id for claim in claims] == [
            "fresh-pending",
            "old-verified",
        ]

        cursor = await conn.execute(
            f"""
            SELECT status, next_attempt_at, lease_token
            FROM {QUEUE_TABLE}
            WHERE item_id = 'recent-verified'
            """
        )
        recent = await cursor.fetchone()
        assert recent["status"] == "verified_markdown"
        assert recent["next_attempt_at"] == due
        assert recent["lease_token"] == ""
        await conn.close()

    asyncio.run(run())


def test_verified_row_without_verified_timestamp_remains_recoverable() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        due = (now - timedelta(minutes=1)).isoformat()
        await conn.execute(
            f"""
            INSERT INTO {QUEUE_TABLE} (
                item_id, first_seen_at, last_seen_at,
                status, verified_at, next_attempt_at
            ) VALUES ('missing-timestamp', ?, ?, 'verified_markdown', NULL, ?)
            """,
            (
                (now - timedelta(days=2)).isoformat(),
                now.isoformat(),
                due,
            ),
        )
        await conn.commit()

        claims = await claim_due_rows_batched(conn, now=now, limit=1)
        assert [claim.item_id for claim in claims] == ["missing-timestamp"]
        await conn.close()

    asyncio.run(run())
