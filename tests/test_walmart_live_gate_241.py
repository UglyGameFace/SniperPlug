from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services.walmart_exact_queue_drain import claim_due_rows_batched
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
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


async def _insert_due_row(
    conn,
    *,
    item_id: str,
    status: str,
    now: datetime,
    priority_score: int,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id,
            priority_score,
            first_seen_at,
            last_seen_at,
            status,
            next_attempt_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            priority_score,
            (now - timedelta(days=1)).isoformat(),
            now.isoformat(),
            status,
            (now - timedelta(seconds=1)).isoformat(),
        ),
    )


def test_open_circuit_claim_cap_zero_blocks_page_rows_without_rescheduling_them() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)

        for index in range(70):
            await _insert_due_row(
                conn,
                item_id=f"page-{index:03d}",
                status="page_retry",
                now=now,
                priority_score=1000 - index,
            )
        await _insert_due_row(
            conn,
            item_id="pending-api-work",
            status="pending",
            now=now,
            priority_score=1,
        )
        await conn.commit()

        cursor = await conn.execute(
            f"""
            SELECT item_id, next_attempt_at
            FROM {QUEUE_TABLE}
            WHERE status = 'page_retry'
            ORDER BY item_id
            """
        )
        before = await cursor.fetchall()

        claims = await claim_due_rows_batched(
            conn,
            now=now,
            limit=8,
            page_recovery_claim_cap=0,
        )
        assert [claim.item_id for claim in claims] == ["pending-api-work"]

        cursor = await conn.execute(
            f"""
            SELECT item_id, next_attempt_at
            FROM {QUEUE_TABLE}
            WHERE status = 'page_retry'
            ORDER BY item_id
            """
        )
        assert await cursor.fetchall() == before

        cursor = await conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {QUEUE_TABLE}
            WHERE status = 'verifying' AND item_id LIKE 'page-%'
            """
        )
        assert (await cursor.fetchone())[0] == 0
        await conn.close()

    asyncio.run(run())


def test_default_claim_cap_still_allows_bounded_page_recovery_when_circuit_closed() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)

        for index in range(5):
            await _insert_due_row(
                conn,
                item_id=f"page-{index}",
                status="page_retry",
                now=now,
                priority_score=100 - index,
            )
        await conn.commit()

        claims = await claim_due_rows_batched(conn, now=now, limit=8)
        assert len(claims) == 2
        assert all(claim.item_id.startswith("page-") for claim in claims)
        await conn.close()

    asyncio.run(run())


def test_production_bulk_runtime_disables_page_recovery_claim_lane_only_when_open() -> None:
    assert "PAGE_RECOVERY_CLAIM_CAP" in BULK_RUNTIME
    assert "0 if page_circuit_remaining > 0 else PAGE_RECOVERY_CLAIM_CAP" in BULK_RUNTIME
    assert "page_recovery_claim_cap=page_recovery_claim_cap" in BULK_RUNTIME
    assert "defer_due_page_recovery_for_open_circuit" not in BULK_RUNTIME
    assert "page_circuit_reschedule_rows=0" in BULK_RUNTIME
    assert BULK_RUNTIME.index("page_circuit_remaining =") < BULK_RUNTIME.index(
        "page_recovery_claim_cap ="
    ) < BULK_RUNTIME.index("claim_due_rows_batched(")
