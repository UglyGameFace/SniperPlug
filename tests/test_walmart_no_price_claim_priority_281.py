from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services.walmart_exact_queue_drain import claim_due_rows_batched
from sniperplug.services.walmart_exact_runtime_schema import ensure_exact_runtime_schema_once
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE


CONFIRMED_NO_PRICE_ERROR = (
    "retry_kind=api_price_missing recovery_kind=api_price_missing"
)
ORDINARY_RETRY_ERROR = "retry_kind=api_http_400"


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _insert_retry(
    conn,
    *,
    item_id: str,
    now: datetime,
    last_error: str,
    last_attempt_at: datetime,
    next_attempt_at: datetime,
    priority: int,
    last_seen_at: datetime | None = None,
) -> None:
    seen = last_seen_at or now
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id,
            priority_score,
            first_seen_at,
            last_seen_at,
            status,
            attempt_count,
            last_attempt_at,
            next_attempt_at,
            last_error
        ) VALUES (?, ?, ?, ?, 'retry', 1, ?, ?, ?)
        """,
        (
            item_id,
            priority,
            seen.isoformat(),
            seen.isoformat(),
            last_attempt_at.isoformat(),
            next_attempt_at.isoformat(),
            last_error,
        ),
    )


def test_ordinary_retry_claims_beat_mature_no_price_even_with_lower_priority() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_exact_runtime_schema_once(db)
        now = datetime.now(timezone.utc)

        for index in range(3):
            last_attempt = now - timedelta(hours=7)
            await _insert_retry(
                conn,
                item_id=f"no-price-{index}",
                now=now,
                last_error=CONFIRMED_NO_PRICE_ERROR,
                last_attempt_at=last_attempt,
                next_attempt_at=last_attempt + timedelta(hours=6),
                priority=10_000 - index,
            )

        for index in range(2):
            await _insert_retry(
                conn,
                item_id=f"ordinary-{index}",
                now=now,
                last_error=ORDINARY_RETRY_ERROR,
                last_attempt_at=now - timedelta(hours=1),
                next_attempt_at=now - timedelta(minutes=5),
                priority=1 + index,
                last_seen_at=now - timedelta(hours=1),
            )
        await conn.commit()

        claims = await claim_due_rows_batched(conn, now=now, limit=2)

        assert [claim.item_id for claim in claims] == ["ordinary-1", "ordinary-0"]
        await conn.close()

    asyncio.run(run())


def test_mature_no_price_retries_fill_only_unused_batch_capacity() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_exact_runtime_schema_once(db)
        now = datetime.now(timezone.utc)

        await _insert_retry(
            conn,
            item_id="ordinary",
            now=now,
            last_error=ORDINARY_RETRY_ERROR,
            last_attempt_at=now - timedelta(hours=1),
            next_attempt_at=now - timedelta(minutes=5),
            priority=1,
        )
        for index in range(3):
            last_attempt = now - timedelta(hours=7)
            await _insert_retry(
                conn,
                item_id=f"no-price-{index}",
                now=now,
                last_error=CONFIRMED_NO_PRICE_ERROR,
                last_attempt_at=last_attempt,
                next_attempt_at=last_attempt + timedelta(hours=6),
                priority=100 - index,
            )
        await conn.commit()

        claims = await claim_due_rows_batched(conn, now=now, limit=3)

        assert claims[0].item_id == "ordinary"
        assert {claim.item_id for claim in claims[1:]} == {"no-price-0", "no-price-1"}
        await conn.close()

    asyncio.run(run())


def test_shortened_dual_no_price_schedule_stays_in_ordinary_retry_lane() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_exact_runtime_schema_once(db)
        now = datetime.now(timezone.utc)

        shortened_last_attempt = now - timedelta(hours=1)
        await _insert_retry(
            conn,
            item_id="woken-no-price",
            now=now,
            last_error=CONFIRMED_NO_PRICE_ERROR,
            last_attempt_at=shortened_last_attempt,
            next_attempt_at=now - timedelta(minutes=5),
            priority=1,
        )

        mature_last_attempt = now - timedelta(hours=7)
        await _insert_retry(
            conn,
            item_id="mature-no-price",
            now=now,
            last_error=CONFIRMED_NO_PRICE_ERROR,
            last_attempt_at=mature_last_attempt,
            next_attempt_at=mature_last_attempt + timedelta(hours=6),
            priority=99_999,
        )
        await conn.commit()

        claims = await claim_due_rows_batched(conn, now=now, limit=1)

        assert [claim.item_id for claim in claims] == ["woken-no-price"]
        await conn.close()

    asyncio.run(run())
