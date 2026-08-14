from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.services.walmart_exact_queue_pressure import (
    bounded_pressure_summary,
    load_walmart_exact_queue_pressure,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(
    encoding="utf-8"
)
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
).read_text(encoding="utf-8")
PRESSURE = (
    ROOT / "sniperplug/services/walmart_exact_queue_pressure.py"
).read_text(encoding="utf-8")


class PressureCursor:
    async def fetchone(self):
        return {
            "initial_due_now": 11,
            "recovery_due_now": 23,
            "recheck_due_now": 319,
            "verifying": 2,
        }


class PressureConnection:
    def __init__(self) -> None:
        self.sql = ""
        self.params = ()

    async def execute(self, sql, params=()):
        self.sql = str(sql)
        self.params = tuple(params)
        return PressureCursor()


class PressureDatabase:
    def __init__(self, conn: PressureConnection) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_bounded_pressure_returns_only_scheduling_counts() -> None:
    conn = PressureConnection()

    health = asyncio.run(
        load_walmart_exact_queue_pressure(PressureDatabase(conn), cap=600)
    )

    assert health.initial_due_now == 11
    assert health.recovery_due_now == 23
    assert health.recheck_due_now == 319
    assert health.due_now == 353
    assert health.verifying == 2
    assert health.total == 355
    assert conn.sql.count("LIMIT ?") == 4
    assert conn.params[3] == 64
    assert conn.params[7] == 64
    assert conn.params[12] == 600
    assert conn.params[14] == 64
    assert conn.params[11] < conn.params[8]
    assert "verified_at IS NULL OR verified_at <= ?" in conn.sql
    assert "SELECT *" not in conn.sql.upper()
    assert "identity_mismatch" not in conn.sql


def test_exact_worker_can_request_a_tighter_all_lane_cap() -> None:
    conn = PressureConnection()

    asyncio.run(load_walmart_exact_queue_pressure(PressureDatabase(conn), cap=64))

    assert conn.params[3] == 64
    assert conn.params[7] == 64
    assert conn.params[12] == 64
    assert conn.params[14] == 64
    assert conn.params[11] < conn.params[8]


def test_bounded_pressure_summary_does_not_claim_unmeasured_totals() -> None:
    conn = PressureConnection()
    health = asyncio.run(load_walmart_exact_queue_pressure(PressureDatabase(conn)))

    summary = bounded_pressure_summary(health)

    assert "actionable due **353**" in summary
    assert "new/retry **11**" in summary
    assert "page recovery **23**" in summary
    assert "scheduled rechecks **319**" in summary
    assert "actively verifying **2**" in summary
    assert "terminal" not in summary
    assert "total" not in summary


def test_background_workers_never_call_complete_queue_health() -> None:
    assert "load_walmart_exact_queue_pressure" in RUNNER
    assert "load_walmart_exact_queue_health" not in RUNNER
    assert "bounded_queue_pressure=true" in RUNNER
    assert "background_full_queue_scans=false" in RUNNER
    assert "load_walmart_exact_queue_pressure" in BULK_RUNTIME
    assert "load_walmart_exact_queue_health" not in BULK_RUNTIME
    assert "_pending_total" not in BULK_RUNTIME
    assert "EXACT_WORKER_PRESSURE_CAP = 64" in BULK_RUNTIME
    assert "cap=EXACT_WORKER_PRESSURE_CAP" in BULK_RUNTIME


def test_pressure_query_is_capped_to_policy_useful_work() -> None:
    assert PRESSURE.count("LIMIT ?") == 4
    assert "DEFAULT_PRESSURE_CAP = 600" in PRESSURE
    assert "FRESH_PRESSURE_CAP = 64" in PRESSURE
    assert "RECOVERY_PRESSURE_CAP = 64" in PRESSURE
    assert "VERIFYING_PRESSURE_CAP = 64" in PRESSURE
    assert "status IN ('pending', 'retry', 'failed', 'verifying')" in PRESSURE
    assert "'verified_markdown'" in PRESSURE
    assert "verified_at IS NULL OR verified_at <= ?" in PRESSURE
    assert "lease_until >= ?" in PRESSURE
