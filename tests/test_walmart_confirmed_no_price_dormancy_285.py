from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_confirmed_no_price_dormancy import (
    CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT,
    CONFIRMED_NO_PRICE_DORMANCY_DAYS,
    CONFIRMED_NO_PRICE_DORMANCY_MARKER,
    park_mature_confirmed_no_price_retries,
)
from sniperplug.services.walmart_exact_queue_bulk_runtime import (
    process_actionable_walmart_exact_queue_batch,
)
from sniperplug.services.walmart_exact_runtime_schema import ensure_exact_runtime_schema_once
from sniperplug.services.walmart_exact_verification_queue import (
    CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS,
    QUEUE_TABLE,
)
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


async def _new_db():
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    await ensure_exact_runtime_schema_once(db)
    return conn, db


async def _insert_retry(
    conn,
    *,
    item_id: str,
    now: datetime,
    next_attempt_at: datetime,
    last_attempt_at: datetime,
    error: str = CONFIRMED_NO_PRICE_ERROR,
    lease_until: datetime | None = None,
    current_cents: int = 2000,
    reference_cents: int = 10000,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id, priority_score, apparent_current_cents,
            apparent_reference_cents, apparent_discount_bps,
            title, product_url, image_url, route_hint, source_label,
            discovered_count, first_seen_at, last_seen_at,
            status, attempt_count, last_attempt_at, next_attempt_at,
            last_error, lease_token, lease_until
        ) VALUES (?, 100, ?, ?, 8000, ?, ?, '', '', 'test', 1, ?, ?,
                  'retry', 1, ?, ?, ?, '', ?)
        """,
        (
            item_id,
            current_cents,
            reference_cents,
            f"Item {item_id}",
            f"https://www.walmart.com/ip/{item_id}",
            _iso(now - timedelta(days=1)),
            _iso(now),
            _iso(last_attempt_at),
            _iso(next_attempt_at),
            error,
            _iso(lease_until) if lease_until is not None else None,
        ),
    )


def _candidate(item_id: str, *, current: float = 20.0) -> SourceCandidate:
    reference = 100.0
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        api_reference_path="search.wasPrice",
        api_discount_percent=round((reference - current) / reference * 100, 2),
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        variant_attributes={"finderSourceQuery": "electronics clearance"},
    )


def test_mature_untouched_six_hour_no_price_schedule_is_parked() -> None:
    async def run() -> None:
        conn, _db = await _new_db()
        now = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
        try:
            last_attempt = now - timedelta(hours=7)
            original_due = last_attempt + timedelta(
                seconds=CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS
            )
            await _insert_retry(
                conn,
                item_id="285001",
                now=now,
                last_attempt_at=last_attempt,
                next_attempt_at=original_due,
            )
            await conn.commit()

            parked = await park_mature_confirmed_no_price_retries(conn, now=now)
            assert parked == 1

            cursor = await conn.execute(
                f"SELECT status, next_attempt_at, last_error FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("285001",),
            )
            status, next_attempt_at, last_error = await cursor.fetchone()
            assert status == "retry"
            assert last_error == CONFIRMED_NO_PRICE_ERROR
            parsed = datetime.fromisoformat(next_attempt_at)
            assert parsed >= now + timedelta(days=CONFIRMED_NO_PRICE_DORMANCY_DAYS)
        finally:
            await conn.close()

    asyncio.run(run())


def test_late_material_wake_is_not_mistaken_for_untouched_six_hour_schedule() -> None:
    async def run() -> None:
        conn, _db = await _new_db()
        now = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
        try:
            # The previous exact attempt was eight hours ago, but discovery woke
            # this row one minute ago. It is later than the old six-hour floor,
            # yet it is not the exact untouched #277 schedule and must stay due.
            await _insert_retry(
                conn,
                item_id="285002",
                now=now,
                last_attempt_at=now - timedelta(hours=8),
                next_attempt_at=now - timedelta(minutes=1),
            )
            await conn.commit()

            parked = await park_mature_confirmed_no_price_retries(conn, now=now)
            assert parked == 0
            cursor = await conn.execute(
                f"SELECT next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("285002",),
            )
            assert (await cursor.fetchone())[0] == _iso(now - timedelta(minutes=1))
        finally:
            await conn.close()

    asyncio.run(run())


def test_active_lease_and_unrelated_retry_are_not_parked() -> None:
    async def run() -> None:
        conn, _db = await _new_db()
        now = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
        try:
            last_attempt = now - timedelta(hours=7)
            due = last_attempt + timedelta(
                seconds=CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS
            )
            await _insert_retry(
                conn,
                item_id="285003",
                now=now,
                last_attempt_at=last_attempt,
                next_attempt_at=due,
                lease_until=now + timedelta(minutes=3),
            )
            await _insert_retry(
                conn,
                item_id="285004",
                now=now,
                last_attempt_at=last_attempt,
                next_attempt_at=due,
                error="retry_kind=api_http_500 upstream transient",
            )
            await conn.commit()

            assert await park_mature_confirmed_no_price_retries(conn, now=now) == 0
        finally:
            await conn.close()

    asyncio.run(run())


def test_dormancy_pass_is_bounded_and_drains_existing_wave_in_chunks() -> None:
    async def run() -> None:
        conn, _db = await _new_db()
        now = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
        try:
            last_attempt = now - timedelta(hours=7)
            due = last_attempt + timedelta(
                seconds=CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS
            )
            total = CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT + 5
            for index in range(total):
                await _insert_retry(
                    conn,
                    item_id=f"2851{index:04d}",
                    now=now,
                    last_attempt_at=last_attempt,
                    next_attempt_at=due,
                )
            await conn.commit()

            first = await park_mature_confirmed_no_price_retries(conn, now=now)
            second = await park_mature_confirmed_no_price_retries(conn, now=now)
            third = await park_mature_confirmed_no_price_retries(conn, now=now)
            assert first == CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT
            assert second == 5
            assert third == 0
        finally:
            await conn.close()

    asyncio.run(run())


def test_material_catalog_change_wakes_a_parked_row_immediately() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        now = datetime.now(timezone.utc)
        try:
            last_attempt = now - timedelta(hours=7)
            due = last_attempt + timedelta(
                seconds=CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS
            )
            await _insert_retry(
                conn,
                item_id="285005",
                now=now,
                last_attempt_at=last_attempt,
                next_attempt_at=due,
                current_cents=2000,
            )
            await conn.commit()
            assert await park_mature_confirmed_no_price_retries(conn, now=now) == 1

            before = await conn.execute(
                f"SELECT next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("285005",),
            )
            dormant_until = (await before.fetchone())[0]

            result = await enqueue_walmart_exact_verification_candidates_bulk(
                db,
                [_candidate("285005", current=15.0)],
                min_discount=50,
                source_label="global_catalog_autoscan:global_catalog_autoscan",
            )
            assert result.persisted_rows == 1

            after = await conn.execute(
                f"SELECT apparent_current_cents, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("285005",),
            )
            current_cents, next_attempt_at = await after.fetchone()
            assert current_cents == 1500
            assert next_attempt_at < dormant_until
            assert datetime.fromisoformat(next_attempt_at) <= datetime.now(timezone.utc) + timedelta(seconds=2)
        finally:
            await conn.close()

    asyncio.run(run())


def test_runtime_wires_dormancy_before_pressure_and_claims() -> None:
    # Static ordering guard protects the production sequence without mocking the
    # entire exact provider stack.
    import inspect

    source = inspect.getsource(process_actionable_walmart_exact_queue_batch)
    park_at = source.index("await park_mature_confirmed_no_price_retries")
    pressure_at = source.index("pressure_before = await load_walmart_exact_queue_pressure")
    claim_at = source.index("claims = await claim_due_rows_batched")
    assert park_at < pressure_at < claim_at
    assert CONFIRMED_NO_PRICE_DORMANCY_MARKER == "confirmed_no_price_dormancy_v285"
