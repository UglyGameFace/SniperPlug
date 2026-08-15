from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_terminal_identity_maintenance import (
    maintain_terminal_identity_rows_limited,
)


ROOT = Path(__file__).resolve().parents[1]
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
).read_text(encoding="utf-8")
PACING_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_fetch_pacing.py"
).read_text(encoding="utf-8")
RUNTIME_SCHEMA = (
    ROOT / "sniperplug/services/walmart_exact_runtime_schema.py"
).read_text(encoding="utf-8")


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _insert_queue_row(
    conn,
    *,
    item_id: str,
    status: str,
    now: datetime,
    last_seen_hours_ago: int = 1,
    next_attempt_hours_ago: int = 1,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id, priority_score, apparent_current_cents,
            apparent_reference_cents, apparent_discount_bps,
            title, product_url, source_label,
            first_seen_at, last_seen_at, status,
            attempt_count, next_attempt_at, last_error,
            lease_token, lease_until
        ) VALUES (?, 1, 1000, 2000, 5000, ?, ?, 'test', ?, ?, ?, 0, ?, '', '', NULL)
        """,
        (
            item_id,
            f"Item {item_id}",
            f"https://www.walmart.com/ip/{item_id}",
            (now - timedelta(hours=last_seen_hours_ago)).isoformat(),
            (now - timedelta(hours=last_seen_hours_ago)).isoformat(),
            status,
            (now - timedelta(hours=next_attempt_hours_ago)).isoformat(),
        ),
    )


def test_terminal_maintenance_is_bounded_and_rearms_small_due_sample() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)

        for index in range(8):
            await _insert_queue_row(
                conn,
                item_id=f"terminal-{index}",
                status="incomplete_identity",
                now=now,
            )
        await conn.commit()

        result = await maintain_terminal_identity_rows_limited(
            db,
            now=now,
            limit=3,
            reprobe_age_days=0,
        )
        assert result.rearmed == 3

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'pending' AND item_id LIKE 'terminal-%'"
        )
        assert (await cursor.fetchone())[0] == 3
        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'incomplete_identity' AND item_id LIKE 'terminal-%'"
        )
        assert (await cursor.fetchone())[0] == 5
        await conn.close()

    asyncio.run(run())


def test_terminal_maintenance_quarantines_recent_terminal_rows_before_reprobe() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)

        for index in range(4):
            await _insert_queue_row(
                conn,
                item_id=f"quarantine-{index}",
                status="identity_mismatch",
                now=now,
            )
        await conn.commit()

        result = await maintain_terminal_identity_rows_limited(
            db,
            now=now,
            limit=2,
            reprobe_age_days=7,
        )
        assert result.quarantined == 2
        assert result.rearmed == 0

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'identity_mismatch' AND next_attempt_at LIKE '9999-12-31%'"
        )
        assert (await cursor.fetchone())[0] == 2
        await conn.close()

    asyncio.run(run())


def test_terminal_maintenance_mixes_quarantine_and_reprobe_with_separate_caps() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)

        for index in range(6):
            await _insert_queue_row(
                conn,
                item_id=f"rearm-{index}",
                status="incomplete_identity",
                now=now - timedelta(days=8),
            )
        for index in range(5):
            await _insert_queue_row(
                conn,
                item_id=f"quarantine-{index}",
                status="identity_mismatch",
                now=now,
            )
        await conn.commit()

        result = await maintain_terminal_identity_rows_limited(
            db,
            now=now,
            limit=4,
            reprobe_age_days=7,
        )
        assert result.rearmed == 4
        assert result.quarantined == 3

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'pending' AND item_id LIKE 'rearm-%'"
        )
        assert (await cursor.fetchone())[0] == 4

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'incomplete_identity' AND item_id LIKE 'rearm-%'"
        )
        assert (await cursor.fetchone())[0] == 2

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'identity_mismatch' AND next_attempt_at LIKE '9999-12-31%'"
        )
        assert (await cursor.fetchone())[0] == 3

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'identity_mismatch' AND next_attempt_at <= ?",
            (now.isoformat(),),
        )
        assert (await cursor.fetchone())[0] == 2
        await conn.close()

    asyncio.run(run())


def test_production_runtime_keeps_bounded_api_only_page_lane_without_rescheduling() -> None:
    assert "defer_due_page_recovery_for_open_circuit" not in BULK_RUNTIME
    assert "API_ONLY_PAGE_RECOVERY_CLAIM_CAP" in BULK_RUNTIME
    assert "if page_circuit_remaining > 0" in BULK_RUNTIME
    assert "else PAGE_RECOVERY_CLAIM_CAP" in BULK_RUNTIME
    assert "0 if page_circuit_remaining > 0 else PAGE_RECOVERY_CLAIM_CAP" not in BULK_RUNTIME
    assert "page_circuit_reschedule_rows=0" in BULK_RUNTIME

    # #255 keeps the same bounded API-only page lane but moves the HTML safety
    # boundary into a dedicated helper. The worker must call that helper rather
    # than reverting to the raw canonical fetcher, and the helper must preserve
    # the canonical verifier for fresh/API proof while applying its page gate.
    assert "fetch_exact_candidate_with_page_pacing" in BULK_RUNTIME
    assert "_fetch_exact_candidate_off_loop" not in BULK_RUNTIME
    assert "PAGE_HTML_PROBE_INTERVAL_SECONDS = 60.0" in PACING_RUNTIME
    assert "_ApiOnlyProviderView" in PACING_RUNTIME
    assert "retry_kind=page_pacing_deferred" in PACING_RUNTIME
    assert "_fetch_exact_candidate_off_loop(" in PACING_RUNTIME
    assert "_trip_page_circuit_immediately(provider)" in PACING_RUNTIME


def test_production_terminal_maintenance_uses_row_limited_implementation() -> None:
    assert "maintain_terminal_identity_rows_limited as maintain_terminal_identity_rows" in RUNTIME_SCHEMA
    assert "Bounding both cadence and row count" in RUNTIME_SCHEMA
