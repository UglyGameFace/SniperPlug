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


def test_status_aware_verified_rechecks_are_ignored_until_their_real_floor() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)

        now = datetime.now(timezone.utc)
        due = (now - timedelta(minutes=5)).isoformat()
        last_seen = now.isoformat()
        rows = (
            ("recent-markdown", "verified_markdown", now - timedelta(hours=23)),
            ("old-markdown", "verified_markdown", now - timedelta(hours=25)),
            ("recent-under", "verified_under_threshold", now - timedelta(hours=70)),
            ("old-under", "verified_under_threshold", now - timedelta(hours=73)),
            ("recent-reference", "verified_reference", now - timedelta(hours=70)),
            ("old-reference", "verified_reference", now - timedelta(hours=73)),
            ("recent-no-reference", "verified_no_reference", now - timedelta(days=6)),
            ("old-no-reference", "verified_no_reference", now - timedelta(days=8)),
            ("recent-unavailable", "not_buyable", now - timedelta(days=6)),
            ("old-unavailable", "not_buyable", now - timedelta(days=8)),
            ("fresh-pending", "pending", None),
        )
        for item_id, status, verified_at in rows:
            verified_text = verified_at.isoformat() if verified_at is not None else None
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, priority_score, first_seen_at, last_seen_at,
                    status, verified_at, last_attempt_at, next_attempt_at
                ) VALUES (?, 100, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    (now - timedelta(days=10)).isoformat(),
                    last_seen,
                    status,
                    verified_text,
                    verified_text,
                    due,
                ),
            )
        await conn.commit()

        pressure = await load_walmart_exact_queue_pressure(db, cap=64)
        assert pressure.initial_due_now == 1
        assert pressure.recheck_due_now == 5
        assert pressure.due_now == 6

        health = await load_walmart_exact_queue_health(db)
        assert health.initial_due_now == 1
        assert health.recheck_due_now == 5
        assert health.due_now == 6

        claims = await claim_due_rows_batched(conn, now=now, limit=20)
        assert [claim.item_id for claim in claims] == [
            "fresh-pending",
            "old-markdown",
            "old-under",
            "old-no-reference",
            "old-reference",
            "old-unavailable",
        ]

        cursor = await conn.execute(
            f"""
            SELECT item_id, status, next_attempt_at, lease_token
            FROM {QUEUE_TABLE}
            WHERE item_id IN (
                'recent-markdown', 'recent-under', 'recent-reference',
                'recent-no-reference', 'recent-unavailable'
            )
            ORDER BY item_id
            """
        )
        suppressed = await cursor.fetchall()
        assert len(suppressed) == 5
        for row in suppressed:
            assert row["next_attempt_at"] == due
            assert row["lease_token"] == ""
            assert row["status"] != "verifying"
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
            ) VALUES ('missing-timestamp', ?, ?, 'verified_no_reference', NULL, ?)
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
