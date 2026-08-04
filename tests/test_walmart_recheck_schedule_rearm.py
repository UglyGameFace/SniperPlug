from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_recheck_schedule_rearm import (
    rearm_legacy_due_rechecks_bounded,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_legacy_hourly_rechecks_are_deferred_only_when_not_truly_due() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await ensure_walmart_exact_verification_queue(FakeDatabase(conn))
        now = datetime(2026, 8, 4, 16, 0, tzinfo=timezone.utc)
        old_due = (now - timedelta(minutes=5)).isoformat()

        rows = (
            (
                "recent-strong",
                "verified_markdown",
                6000,
                (now - timedelta(hours=1)).isoformat(),
            ),
            (
                "old-strong",
                "verified_markdown",
                6000,
                (now - timedelta(hours=5)).isoformat(),
            ),
            (
                "fresh-work",
                "pending",
                0,
                (now - timedelta(minutes=10)).isoformat(),
            ),
        )
        for item_id, status, discount_bps, verified_at in rows:
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, first_seen_at, last_seen_at, status,
                    exact_discount_bps, verified_at, last_attempt_at,
                    next_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    (now - timedelta(days=1)).isoformat(),
                    now.isoformat(),
                    status,
                    discount_bps,
                    verified_at,
                    verified_at,
                    old_due,
                ),
            )
        await conn.commit()

        updated = await rearm_legacy_due_rechecks_bounded(conn, now=now)
        assert updated == 1

        cursor = await conn.execute(
            f"SELECT item_id, next_attempt_at FROM {QUEUE_TABLE} ORDER BY item_id"
        )
        next_by_id = {item_id: value for item_id, value in await cursor.fetchall()}

        assert next_by_id["recent-strong"] == (
            now + timedelta(hours=3)
        ).isoformat()
        assert next_by_id["old-strong"] == old_due
        assert next_by_id["fresh-work"] == old_due
        assert await rearm_legacy_due_rechecks_bounded(conn, now=now) == 0
        await conn.close()

    asyncio.run(run())
