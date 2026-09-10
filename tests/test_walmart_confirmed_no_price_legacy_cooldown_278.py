from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services.walmart_confirmed_no_price_legacy_cooldown import (
    normalize_legacy_confirmed_no_price_cooldowns,
)
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


async def _new_db():
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    await ensure_walmart_exact_verification_queue(db)
    return conn, db


async def _seed_retry(
    conn,
    *,
    item_id: str,
    first_seen: datetime,
    last_seen: datetime,
    last_attempt: datetime,
    next_attempt: datetime,
    error: str = CONFIRMED_NO_PRICE_ERROR,
    lease_until: datetime | None = None,
) -> None:
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
            last_error,
            lease_token,
            lease_until
        ) VALUES (?, ?, ?, 'retry', 1, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            first_seen.isoformat(),
            last_seen.isoformat(),
            last_attempt.isoformat(),
            next_attempt.isoformat(),
            error,
            "leased" if lease_until is not None else "",
            lease_until.isoformat() if lease_until is not None else None,
        ),
    )
    await conn.commit()


async def _next_attempt(conn, item_id: str) -> str:
    cursor = await conn.execute(
        f"SELECT next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
        (item_id,),
    )
    row = await cursor.fetchone()
    return str(row[0])


def test_legacy_confirmed_no_price_retry_is_lifted_to_original_six_hour_floor() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            now = datetime(2026, 9, 10, 1, 15, tzinfo=timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            old_next = last_attempt + timedelta(minutes=5)
            await _seed_retry(
                conn,
                item_id="920001",
                first_seen=last_attempt - timedelta(hours=2),
                last_seen=last_attempt - timedelta(minutes=1),
                last_attempt=last_attempt,
                next_attempt=old_next,
            )

            changed = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                now=now,
            )

            assert changed == 1
            assert await _next_attempt(conn, "920001") == (
                last_attempt + timedelta(hours=6)
            ).isoformat()
        finally:
            await conn.close()

    asyncio.run(run())


def test_post_attempt_rediscovery_is_not_delayed() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            now = datetime(2026, 9, 10, 1, 15, tzinfo=timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            old_next = last_attempt + timedelta(minutes=5)
            await _seed_retry(
                conn,
                item_id="920002",
                first_seen=last_attempt - timedelta(hours=2),
                last_seen=last_attempt + timedelta(minutes=1),
                last_attempt=last_attempt,
                next_attempt=old_next,
            )

            changed = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                now=now,
            )

            assert changed == 0
            assert await _next_attempt(conn, "920002") == old_next.isoformat()
        finally:
            await conn.close()

    asyncio.run(run())


def test_active_lease_is_not_rescheduled() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            now = datetime(2026, 9, 10, 1, 15, tzinfo=timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            old_next = last_attempt + timedelta(minutes=5)
            await _seed_retry(
                conn,
                item_id="920003",
                first_seen=last_attempt - timedelta(hours=2),
                last_seen=last_attempt - timedelta(minutes=1),
                last_attempt=last_attempt,
                next_attempt=old_next,
                lease_until=now + timedelta(minutes=5),
            )

            changed = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                now=now,
            )

            assert changed == 0
            assert await _next_attempt(conn, "920003") == old_next.isoformat()
        finally:
            await conn.close()

    asyncio.run(run())


def test_expired_six_hour_floor_is_not_restarted_from_deploy_time() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            now = datetime(2026, 9, 10, 1, 15, tzinfo=timezone.utc)
            last_attempt = now - timedelta(hours=7)
            old_next = last_attempt + timedelta(minutes=5)
            await _seed_retry(
                conn,
                item_id="920004",
                first_seen=last_attempt - timedelta(hours=2),
                last_seen=last_attempt - timedelta(minutes=1),
                last_attempt=last_attempt,
                next_attempt=old_next,
            )

            changed = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                now=now,
            )

            assert changed == 0
            assert await _next_attempt(conn, "920004") == old_next.isoformat()
        finally:
            await conn.close()

    asyncio.run(run())


def test_existing_six_hour_or_later_schedule_is_left_unchanged() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            now = datetime(2026, 9, 10, 1, 15, tzinfo=timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            existing_next = last_attempt + timedelta(hours=6)
            await _seed_retry(
                conn,
                item_id="920005",
                first_seen=last_attempt - timedelta(hours=2),
                last_seen=last_attempt - timedelta(minutes=1),
                last_attempt=last_attempt,
                next_attempt=existing_next,
            )

            changed = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                now=now,
            )

            assert changed == 0
            assert await _next_attempt(conn, "920005") == existing_next.isoformat()
        finally:
            await conn.close()

    asyncio.run(run())


def test_non_dual_no_price_retry_keeps_ordinary_schedule() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            now = datetime(2026, 9, 10, 1, 15, tzinfo=timezone.utc)
            last_attempt = now - timedelta(minutes=30)
            old_next = last_attempt + timedelta(minutes=5)
            await _seed_retry(
                conn,
                item_id="920006",
                first_seen=last_attempt - timedelta(hours=2),
                last_seen=last_attempt - timedelta(minutes=1),
                last_attempt=last_attempt,
                next_attempt=old_next,
                error="retry_kind=api_http_400",
            )

            changed = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                now=now,
            )

            assert changed == 0
            assert await _next_attempt(conn, "920006") == old_next.isoformat()
        finally:
            await conn.close()

    asyncio.run(run())
