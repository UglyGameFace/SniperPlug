from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services.walmart_exact_queue_drain import (
    _indexed_due_rows_select_params,
    _indexed_due_rows_select_sql,
    claim_due_rows_batched,
    verified_recheck_cutoffs,
)
from sniperplug.services.walmart_exact_queue_pressure import (
    load_walmart_exact_queue_pressure,
)
from sniperplug.services.walmart_exact_runtime_schema import (
    FRESH_PROOF_DUE_INDEX,
    STATUS_DUE_INDEX,
    STATUS_VERIFIED_DUE_INDEX,
    ensure_exact_runtime_schema_once,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
)
from sniperplug.services.walmart_fresh_discovery_proof import (
    DISCOVERY_PROOF_MAX_AGE_SECONDS,
)


class FakeDatabase:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


class CaptureConnection:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.sql = ""
        self.params: tuple = ()

    async def execute(self, sql, params=()):
        self.sql = str(sql)
        self.params = tuple(params)
        return await self.inner.execute(sql, params)


async def _insert_queue_fixture(conn, now: datetime) -> None:
    due = (now - timedelta(minutes=5)).isoformat()
    recent_verified = (now - timedelta(days=1)).isoformat()
    old_verified = (now - timedelta(days=8)).isoformat()
    first_seen = (now - timedelta(days=10)).isoformat()
    last_seen = now.isoformat()

    await conn.executemany(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id, priority_score, first_seen_at, last_seen_at,
            status, verified_at, next_attempt_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                f"recent-low-{index:04d}",
                1000 - index,
                first_seen,
                last_seen,
                "verified_no_reference",
                recent_verified,
                due,
            )
            for index in range(256)
        ]
        + [
            (
                "old-low",
                500,
                first_seen,
                last_seen,
                "verified_no_reference",
                old_verified,
                due,
            ),
            (
                "fresh-pending",
                900,
                first_seen,
                last_seen,
                "pending",
                None,
                due,
            ),
        ],
    )
    await conn.commit()


def test_claim_plan_seeks_lanes_instead_of_scanning_recent_verified_rows() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_exact_runtime_schema_once(db)
        now = datetime.now(timezone.utc)
        await _insert_queue_fixture(conn, now)

        now_iso = now.isoformat()
        discovery_cutoff = (
            now - timedelta(days=QUEUE_RETENTION_DAYS)
        ).isoformat()
        snapshot_cutoff = (
            now - timedelta(seconds=DISCOVERY_PROOF_MAX_AGE_SECONDS)
        ).isoformat()
        markdown_cutoff, reference_cutoff, low_signal_cutoff = (
            verified_recheck_cutoffs(now)
        )
        sql = _indexed_due_rows_select_sql()
        params = _indexed_due_rows_select_params(
            now_iso=now_iso,
            discovery_cutoff=discovery_cutoff,
            snapshot_cutoff=snapshot_cutoff,
            markdown_recheck_cutoff=markdown_cutoff,
            reference_recheck_cutoff=reference_cutoff,
            low_signal_recheck_cutoff=low_signal_cutoff,
            bounded_limit=8,
            page_recovery_claim_cap=0,
        )
        cursor = await conn.execute("EXPLAIN QUERY PLAN " + sql, params)
        plan = "\n".join(str(row[3]) for row in await cursor.fetchall())

        assert f"USING INDEX {FRESH_PROOF_DUE_INDEX}" in plan
        assert f"USING INDEX {STATUS_DUE_INDEX}" in plan
        assert f"USING INDEX {STATUS_VERIFIED_DUE_INDEX}" in plan
        assert "status=? AND verified_at<?" in plan
        assert f"SCAN {QUEUE_TABLE}" not in plan

        claims = await claim_due_rows_batched(
            conn,
            now=now,
            limit=8,
            page_recovery_claim_cap=0,
        )
        assert [claim.item_id for claim in claims] == [
            "fresh-pending",
            "old-low",
        ]
        await conn.close()

    asyncio.run(run())


def test_pressure_plan_uses_verified_age_index_and_ignores_legacy_early_floor() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_exact_runtime_schema_once(db)
        now = datetime.now(timezone.utc)
        await _insert_queue_fixture(conn, now)

        capture = CaptureConnection(conn)
        pressure = await load_walmart_exact_queue_pressure(
            FakeDatabase(capture),
            cap=64,
        )
        assert pressure.initial_due_now == 1
        assert pressure.recheck_due_now == 1
        assert pressure.due_now == 2

        cursor = await conn.execute(
            "EXPLAIN QUERY PLAN " + capture.sql,
            capture.params,
        )
        plan = "\n".join(str(row[3]) for row in await cursor.fetchall())
        assert f"USING INDEX {STATUS_DUE_INDEX}" in plan
        assert f"USING INDEX {STATUS_VERIFIED_DUE_INDEX}" in plan
        assert "status=? AND verified_at<?" in plan
        assert f"SCAN {QUEUE_TABLE}" not in plan
        await conn.close()

    asyncio.run(run())
