from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_page_circuit_queue import (
    defer_due_page_recovery_for_open_circuit,
)
from sniperplug.services.walmart_terminal_identity_maintenance import (
    maintain_terminal_identity_rows_limited,
)


ROOT = Path(__file__).resolve().parents[1]
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
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


def test_open_page_circuit_defers_due_page_backlog_without_blocking_pending_work() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)

        await _insert_queue_row(
            conn,
            item_id="page-high",
            status="page_retry",
            now=now,
            priority_score=100,
        )
        await _insert_queue_row(
            conn,
            item_id="page-low",
            status="page_retry",
            now=now,
            priority_score=10,
        )
        await _insert_queue_row(
            conn,
            item_id="rescued-pending",
            status="pending",
            now=now,
            priority_score=1,
        )
        await conn.commit()

        first = await defer_due_page_recovery_for_open_circuit(
            conn,
            now=now,
            remaining_seconds=600,
            limit=1,
        )
        assert first == 1

        cursor = await conn.execute(
            f"SELECT item_id, status, next_attempt_at FROM {QUEUE_TABLE} ORDER BY item_id"
        )
        rows = {row[0]: (row[1], datetime.fromisoformat(row[2])) for row in await cursor.fetchall()}
        assert rows["page-high"][0] == "page_retry"
        assert rows["page-high"][1] > now + timedelta(minutes=9)
        assert rows["page-low"][1] <= now
        assert rows["rescued-pending"][0] == "pending"
        assert rows["rescued-pending"][1] <= now

        second = await defer_due_page_recovery_for_open_circuit(
            conn,
            now=now,
            remaining_seconds=600,
            limit=64,
        )
        assert second == 1

        cursor = await conn.execute(
            f"SELECT status, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
            ("rescued-pending",),
        )
        status, next_attempt_at = await cursor.fetchone()
        assert status == "pending"
        assert datetime.fromisoformat(next_attempt_at) <= now
        await conn.close()

    asyncio.run(run())


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


def test_production_runtime_defers_page_backlog_before_pressure_and_claims() -> None:
    defer_call = "defer_due_page_recovery_for_open_circuit("
    pressure_call = "load_walmart_exact_queue_pressure("
    claim_call = "claim_due_rows_batched("
    assert defer_call in BULK_RUNTIME
    assert BULK_RUNTIME.index(defer_call) < BULK_RUNTIME.index(pressure_call)
    assert BULK_RUNTIME.index(defer_call) < BULK_RUNTIME.index(claim_call)
    assert "page_circuit_remaining > 0" in BULK_RUNTIME


def test_production_terminal_maintenance_uses_row_limited_implementation() -> None:
    assert "maintain_terminal_identity_rows_limited as maintain_terminal_identity_rows" in RUNTIME_SCHEMA
    assert "Bounding both cadence and row count" in RUNTIME_SCHEMA
