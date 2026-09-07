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
    next_attempt_at: datetime | None = None,
    last_attempt_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    priority_score: int = 0,
) -> None:
    first_seen = now - timedelta(days=10)
    seen = last_seen_at or now
    due = next_attempt_at or (now - timedelta(seconds=1))
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id,
            priority_score,
            first_seen_at,
            last_seen_at,
            status,
            last_attempt_at,
            next_attempt_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            priority_score,
            first_seen.isoformat(),
            seen.isoformat(),
            status,
            last_attempt_at.isoformat() if last_attempt_at else None,
            due.isoformat(),
        ),
    )


def test_terminal_identity_maintenance_caps_reprobes_and_quarantine_writes() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        old_attempt = now - timedelta(days=8)
        rediscovered = now - timedelta(hours=1)

        for index in range(6):
            await _insert_queue_row(
                conn,
                item_id=f"rearm-{index}",
                status="incomplete_identity",
                now=now,
                next_attempt_at=datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
                last_attempt_at=old_attempt,
                last_seen_at=rediscovered,
                priority_score=100 - index,
            )

        not_rediscovered_attempt = now - timedelta(days=1)
        for index in range(5):
            await _insert_queue_row(
                conn,
                item_id=f"quarantine-{index}",
                status="identity_mismatch",
                now=now,
                next_attempt_at=now - timedelta(minutes=5),
                last_attempt_at=not_rediscovered_attempt,
                last_seen_at=not_rediscovered_attempt,
            )
        await conn.commit()

        result = await maintain_terminal_identity_rows_limited(
            db,
            now=now,
            rearm_limit=2,
            quarantine_limit=3,
        )
        assert result.rearmed == 2
        assert result.quarantined == 3

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'pending' AND item_id LIKE 'rearm-%'"
        )
        assert (await cursor.fetchone())[0] == 2

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE status = 'incomplete_identity' AND item_id LIKE 'rearm-%'"
        )
        assert (await cursor.fetchone())[0] == 4

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
    assert "page_recovery_claim_cap = API_ONLY_PAGE_RECOVERY_CLAIM_CAP" in BULK_RUNTIME
    assert "elif page_probe_remaining > 0" in BULK_RUNTIME
    assert "page_recovery_claim_cap = 0" in BULK_RUNTIME
    assert "page_recovery_claim_cap = PAGE_RECOVERY_CLAIM_CAP" in BULK_RUNTIME
    assert "0 if page_circuit_remaining > 0 else PAGE_RECOVERY_CLAIM_CAP" not in BULK_RUNTIME
    assert "page_circuit_reschedule_rows=0" in BULK_RUNTIME

    # #255 moved the HTML safety boundary into a dedicated helper. The worker
    # must use that helper while preserving the canonical exact verifier for
    # fresh catalog and official API/exact-search proof. #256 adds a middle
    # paced state that leaves new/API work claimable while old page_retry rows
    # wait for the single HTML probe slot instead of being re-leased just to
    # write another page_pacing_deferred outcome.
    assert "fetch_exact_candidate_with_page_pacing" in BULK_RUNTIME
    assert "page_html_probe_remaining_seconds" in BULK_RUNTIME
    assert "_fetch_exact_candidate_off_loop" not in BULK_RUNTIME
    assert "PAGE_HTML_PROBE_INTERVAL_SECONDS = 60.0" in PACING_RUNTIME
    assert "_ApiOnlyProviderView" in PACING_RUNTIME
    assert "retry_kind=page_pacing_deferred" in PACING_RUNTIME
    assert "_fetch_exact_candidate_off_loop(" in PACING_RUNTIME
    assert "_trip_page_circuit_immediately(provider)" in PACING_RUNTIME


def test_production_terminal_maintenance_uses_row_limited_implementation() -> None:
    assert "maintain_terminal_identity_rows_limited as maintain_terminal_identity_rows" in RUNTIME_SCHEMA
    assert "Bounding both cadence and row count" in RUNTIME_SCHEMA
