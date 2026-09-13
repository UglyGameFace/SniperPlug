from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import aiosqlite

from sniperplug.services.walmart_exact_queue_drain import STATUS_DUE_INDEX
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_page_pacing_debt import (
    PAGE_PACING_DEBT_BATCH_LIMIT,
    PAGE_PACING_DEBT_MARKER,
    PAGE_PACING_DEFERRED_TOKEN,
    PAGE_PACING_SLOT_MARKER,
    normalize_legacy_page_pacing_debt,
    serialize_page_pacing_deferred_outcomes,
)


ROOT = Path(__file__).resolve().parents[1]
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
).read_text(encoding="utf-8")


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _setup_queue():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = FakeDatabase(conn)
    await ensure_walmart_exact_verification_queue(db)
    await conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {STATUS_DUE_INDEX}
        ON {QUEUE_TABLE} (
            status,
            next_attempt_at,
            last_seen_at,
            lease_until,
            priority_score DESC
        )
        """
    )
    await conn.commit()
    return conn


async def _insert_page_row(
    conn,
    *,
    item_id: str,
    now: datetime,
    next_attempt_at: datetime,
    last_error: str,
    last_attempt_at: datetime | None = None,
    lease_until: datetime | None = None,
) -> None:
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
            last_error,
            lease_token,
            lease_until
        ) VALUES (?, 100, ?, ?, 'page_retry', 1, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            (now - timedelta(hours=1)).isoformat(),
            now.isoformat(),
            last_attempt_at.isoformat() if last_attempt_at else None,
            next_attempt_at.isoformat(),
            last_error,
            "leased" if lease_until is not None else "",
            lease_until.isoformat() if lease_until is not None else None,
        ),
    )


async def _load_rows(conn):
    cursor = await conn.execute(
        f"""
        SELECT item_id, next_attempt_at, last_error, lease_until
        FROM {QUEUE_TABLE}
        ORDER BY item_id
        """
    )
    return {row["item_id"]: row for row in await cursor.fetchall()}


def test_new_pacing_deferrals_keep_30_minute_floor_and_serialize_one_minute_slots() -> None:
    async def run() -> None:
        conn = await _setup_queue()
        observed = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        error = (
            "retry_kind=page_pacing_deferred Walmart product-page recovery deferred "
            "by the 60s single-probe pacing guard"
        )
        outcomes = []
        for index in range(3):
            item_id = f"paced-{index}"
            await _insert_page_row(
                conn,
                item_id=item_id,
                now=observed,
                next_attempt_at=observed + timedelta(minutes=30),
                last_error=error,
                last_attempt_at=observed,
            )
            outcomes.append(
                SimpleNamespace(
                    claim=SimpleNamespace(item_id=item_id),
                    candidate=None,
                    error=error,
                    observed_at=observed,
                )
            )
        await conn.commit()

        result = await serialize_page_pacing_deferred_outcomes(conn, outcomes)
        await conn.commit()

        assert result.rows == 3
        assert result.sql_statements == 2
        assert result.first_slot == observed + timedelta(minutes=30)
        assert result.last_slot == observed + timedelta(minutes=32)

        rows = await _load_rows(conn)
        slots = [
            datetime.fromisoformat(rows[f"paced-{index}"]["next_attempt_at"])
            for index in range(3)
        ]
        assert slots == [
            observed + timedelta(minutes=30),
            observed + timedelta(minutes=31),
            observed + timedelta(minutes=32),
        ]
        assert all(
            PAGE_PACING_SLOT_MARKER in rows[f"paced-{index}"]["last_error"]
            for index in range(3)
        )
        assert all(
            PAGE_PACING_DEFERRED_TOKEN in rows[f"paced-{index}"]["last_error"]
            for index in range(3)
        )
        await conn.close()

    asyncio.run(run())


def test_new_pacing_deferral_appends_after_existing_serialized_tail() -> None:
    async def run() -> None:
        conn = await _setup_queue()
        observed = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        error = "retry_kind=page_pacing_deferred Walmart product-page recovery deferred"
        await _insert_page_row(
            conn,
            item_id="existing-tail",
            now=observed,
            next_attempt_at=observed + timedelta(minutes=35),
            last_error=f"{error} marker={PAGE_PACING_SLOT_MARKER}",
        )
        await _insert_page_row(
            conn,
            item_id="new-paced",
            now=observed,
            next_attempt_at=observed + timedelta(minutes=30),
            last_error=error,
            last_attempt_at=observed,
        )
        await conn.commit()

        result = await serialize_page_pacing_deferred_outcomes(
            conn,
            [
                SimpleNamespace(
                    claim=SimpleNamespace(item_id="new-paced"),
                    candidate=None,
                    error=error,
                    observed_at=observed,
                )
            ],
        )
        await conn.commit()

        assert result.rows == 1
        assert result.first_slot == observed + timedelta(minutes=36)
        rows = await _load_rows(conn)
        assert datetime.fromisoformat(rows["new-paced"]["next_attempt_at"]) == (
            observed + timedelta(minutes=36)
        )
        await conn.close()

    asyncio.run(run())


def test_legacy_pacing_herd_is_appended_after_existing_slot_tail_without_touching_real_page_error() -> None:
    async def run() -> None:
        conn = await _setup_queue()
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        pacing_error = (
            "retry_kind=page_pacing_deferred Walmart product-page recovery deferred"
        )
        await _insert_page_row(
            conn,
            item_id="already-slotted",
            now=now,
            next_attempt_at=now + timedelta(minutes=10),
            last_error=f"{pacing_error} marker={PAGE_PACING_SLOT_MARKER}",
        )
        await _insert_page_row(
            conn,
            item_id="legacy-a",
            now=now,
            next_attempt_at=now - timedelta(minutes=5),
            last_error=pacing_error,
        )
        await _insert_page_row(
            conn,
            item_id="legacy-b",
            now=now,
            next_attempt_at=now - timedelta(minutes=4),
            last_error=pacing_error,
        )
        await _insert_page_row(
            conn,
            item_id="real-page-failure",
            now=now,
            next_attempt_at=now - timedelta(minutes=3),
            last_error="retry_kind=page_http_403 Walmart page returned HTTP 403",
        )
        await conn.commit()

        changed = await normalize_legacy_page_pacing_debt(conn, now=now)
        assert changed == 2

        rows = await _load_rows(conn)
        assert datetime.fromisoformat(rows["already-slotted"]["next_attempt_at"]) == (
            now + timedelta(minutes=10)
        )
        assert datetime.fromisoformat(rows["legacy-a"]["next_attempt_at"]) == (
            now + timedelta(minutes=11)
        )
        assert datetime.fromisoformat(rows["legacy-b"]["next_attempt_at"]) == (
            now + timedelta(minutes=12)
        )
        assert PAGE_PACING_SLOT_MARKER in rows["legacy-a"]["last_error"]
        assert PAGE_PACING_SLOT_MARKER in rows["legacy-b"]["last_error"]
        assert rows["real-page-failure"]["last_error"].startswith(
            "retry_kind=page_http_403"
        )
        assert datetime.fromisoformat(rows["real-page-failure"]["next_attempt_at"]) == (
            now - timedelta(minutes=3)
        )

        assert await normalize_legacy_page_pacing_debt(conn, now=now) == 0
        await conn.close()

    asyncio.run(run())


def test_legacy_normalization_preserves_future_not_before_and_skips_active_lease() -> None:
    async def run() -> None:
        conn = await _setup_queue()
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        error = "retry_kind=page_pacing_deferred Walmart product-page recovery deferred"
        future_not_before = now + timedelta(minutes=20)
        await _insert_page_row(
            conn,
            item_id="future-floor",
            now=now,
            next_attempt_at=future_not_before,
            last_error=error,
        )
        await _insert_page_row(
            conn,
            item_id="actively-leased",
            now=now,
            next_attempt_at=now - timedelta(minutes=5),
            last_error=error,
            lease_until=now + timedelta(minutes=5),
        )
        await conn.commit()

        changed = await normalize_legacy_page_pacing_debt(conn, now=now)
        assert changed == 1

        rows = await _load_rows(conn)
        assert datetime.fromisoformat(rows["future-floor"]["next_attempt_at"]) == (
            future_not_before
        )
        assert PAGE_PACING_SLOT_MARKER in rows["future-floor"]["last_error"]
        assert datetime.fromisoformat(rows["actively-leased"]["next_attempt_at"]) == (
            now - timedelta(minutes=5)
        )
        assert PAGE_PACING_SLOT_MARKER not in rows["actively-leased"]["last_error"]
        await conn.close()

    asyncio.run(run())


def test_legacy_normalization_hard_caps_each_batch_even_with_larger_caller_limit() -> None:
    async def run() -> None:
        conn = await _setup_queue()
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        error = "retry_kind=page_pacing_deferred Walmart product-page recovery deferred"
        total_rows = PAGE_PACING_DEBT_BATCH_LIMIT + 5
        for index in range(total_rows):
            await _insert_page_row(
                conn,
                item_id=f"legacy-{index:03d}",
                now=now,
                next_attempt_at=now - timedelta(minutes=5),
                last_error=error,
            )
        await conn.commit()

        changed = await normalize_legacy_page_pacing_debt(
            conn,
            now=now,
            limit=PAGE_PACING_DEBT_BATCH_LIMIT + 999,
        )
        assert changed == PAGE_PACING_DEBT_BATCH_LIMIT

        cursor = await conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN instr(lower(last_error), ?) > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN instr(lower(last_error), ?) = 0 THEN 1 ELSE 0 END)
            FROM {QUEUE_TABLE}
            WHERE instr(lower(last_error), ?) > 0
            """,
            (
                PAGE_PACING_SLOT_MARKER,
                PAGE_PACING_SLOT_MARKER,
                PAGE_PACING_DEFERRED_TOKEN,
            ),
        )
        slotted, unslotted = await cursor.fetchone()
        assert slotted == PAGE_PACING_DEBT_BATCH_LIMIT
        assert unslotted == 5

        assert await normalize_legacy_page_pacing_debt(conn, now=now) == 5
        await conn.close()

    asyncio.run(run())


def test_nonpositive_legacy_limit_performs_no_migration() -> None:
    async def run() -> None:
        conn = await _setup_queue()
        now = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
        error = "retry_kind=page_pacing_deferred Walmart product-page recovery deferred"
        await _insert_page_row(
            conn,
            item_id="legacy-zero-limit",
            now=now,
            next_attempt_at=now - timedelta(minutes=5),
            last_error=error,
        )
        await conn.commit()

        assert await normalize_legacy_page_pacing_debt(conn, now=now, limit=0) == 0
        rows = await _load_rows(conn)
        assert PAGE_PACING_SLOT_MARKER not in rows["legacy-zero-limit"]["last_error"]
        await conn.close()

    asyncio.run(run())


def test_runtime_normalizes_before_pressure_and_serializes_after_canonical_persistence() -> None:
    normalize_call = "await normalize_legacy_page_pacing_debt(conn, now=now)"
    pressure_call = "pressure_before = await load_walmart_exact_queue_pressure("
    persist_call = "persisted = await persist_exact_queue_outcomes_bulk("
    serialize_call = "pacing_schedule = await serialize_page_pacing_deferred_outcomes("
    commit_call = "await conn.commit()"

    assert normalize_call in BULK_RUNTIME
    assert serialize_call in BULK_RUNTIME
    assert PAGE_PACING_SLOT_MARKER in BULK_RUNTIME
    assert PAGE_PACING_DEBT_MARKER not in BULK_RUNTIME  # emitted by the debt helper itself
    assert BULK_RUNTIME.index(normalize_call) < BULK_RUNTIME.index(pressure_call)
    assert BULK_RUNTIME.index(persist_call) < BULK_RUNTIME.index(serialize_call)
    assert BULK_RUNTIME.index(serialize_call) < BULK_RUNTIME.rindex(commit_call)
    assert "page_pacing_slots=%s page_pacing_schedule=%s" in BULK_RUNTIME
