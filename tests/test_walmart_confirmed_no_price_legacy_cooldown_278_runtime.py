from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services.walmart_exact_runtime_schema import ensure_exact_runtime_schema_once
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)


CONFIRMED_NO_PRICE_ERROR = (
    "retry_kind=api_price_missing exact detail did not provide a numeric current price; "
    "recovery_kind=api_price_missing"
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_runtime_schema_applies_legacy_no_price_floor_once() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        try:
            # Build only the base queue schema. The exact-runtime once gate has
            # not run yet, so this reproduces an existing database at deploy.
            await ensure_walmart_exact_verification_queue(db)
            now = datetime.now(timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            old_next = last_attempt + timedelta(minutes=5)
            target = last_attempt + timedelta(hours=6)
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id,
                    first_seen_at,
                    last_seen_at,
                    status,
                    attempt_count,
                    last_attempt_at,
                    next_attempt_at,
                    last_error
                ) VALUES (?, ?, ?, 'retry', 1, ?, ?, ?)
                """,
                (
                    "920100",
                    (last_attempt - timedelta(hours=2)).isoformat(),
                    (last_attempt - timedelta(minutes=1)).isoformat(),
                    last_attempt.isoformat(),
                    old_next.isoformat(),
                    CONFIRMED_NO_PRICE_ERROR,
                ),
            )
            await conn.commit()

            await ensure_exact_runtime_schema_once(db)
            cursor = await conn.execute(
                f"SELECT next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("920100",),
            )
            assert (await cursor.fetchone())[0] == target.isoformat()

            # The existing schema gate remains once-per-connection. Calling it
            # again must not restart or extend the migrated cooldown.
            await ensure_exact_runtime_schema_once(db)
            cursor = await conn.execute(
                f"SELECT next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("920100",),
            )
            assert (await cursor.fetchone())[0] == target.isoformat()
        finally:
            await conn.close()

    asyncio.run(run())
