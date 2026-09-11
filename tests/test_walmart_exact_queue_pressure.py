from __future__ import annotations

import asyncio
from pathlib import Path

import sniperplug.services.walmart_exact_queue_pressure as pressure_module
from sniperplug.services.walmart_exact_queue_pressure import (
    PAGE_RECOVERY_REASON_DIAGNOSTIC_INTERVAL_SECONDS,
    PAGE_RECOVERY_REASON_DIAGNOSTIC_MARKER,
    _reserve_page_recovery_reason_diagnostic,
    bounded_pressure_summary,
    load_due_walmart_page_recovery_reason_sample,
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
            "confirmed_no_price_due_now": 0,
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


class PageReasonCursor:
    def __init__(self, rows) -> None:
        self.rows = list(rows)

    async def fetchall(self):
        return list(self.rows)


class PageReasonConnection:
    def __init__(self, rows) -> None:
        self.rows = list(rows)
        self.sql = ""
        self.params = ()

    async def execute(self, sql, params=()):
        self.sql = str(sql)
        self.params = tuple(params)
        return PageReasonCursor(self.rows)


def test_bounded_pressure_returns_only_scheduling_counts() -> None:
    conn = PressureConnection()

    health = asyncio.run(
        load_walmart_exact_queue_pressure(PressureDatabase(conn), cap=600)
    )

    assert health.initial_due_now == 11
    assert health.confirmed_no_price_due_now == 0
    assert health.recovery_due_now == 23
    assert health.recheck_due_now == 319
    assert health.due_now == 353
    assert health.verifying == 2
    assert health.total == 355
    assert conn.sql.count("LIMIT ?") == 5
    assert conn.params[3] == 64
    assert conn.params[7] == 600
    assert conn.params[11] == 64
    assert conn.params[27] == 600
    assert conn.params[29] == 64
    assert conn.params[20] < conn.params[16] < conn.params[12] < conn.params[0]
    assert "status IN ('verified_markdown')" in conn.sql
    assert "'verified_under_threshold', 'verified_reference'" in conn.sql
    assert "'verified_no_reference', 'not_buyable'" in conn.sql
    assert "verified_at IS NULL" in conn.sql
    assert "confirmed_no_price_due" in conn.sql
    assert "recovery_kind=api_price_missing" in conn.sql
    assert "INDEXED BY idx_walmart_exact_detail_queue_status_due" in conn.sql
    assert "INDEXED BY idx_walmart_exact_detail_queue_status_verified_due" in conn.sql
    assert "SELECT *" not in conn.sql.upper()
    assert "identity_mismatch" not in conn.sql


def test_exact_worker_can_request_a_tighter_all_lane_cap() -> None:
    conn = PressureConnection()

    asyncio.run(load_walmart_exact_queue_pressure(PressureDatabase(conn), cap=64))

    assert conn.params[3] == 64
    assert conn.params[7] == 64
    assert conn.params[11] == 64
    assert conn.params[27] == 64
    assert conn.params[29] == 64
    assert conn.params[20] < conn.params[16] < conn.params[12] < conn.params[0]


def test_bounded_pressure_summary_does_not_claim_unmeasured_totals() -> None:
    conn = PressureConnection()
    health = asyncio.run(load_walmart_exact_queue_pressure(PressureDatabase(conn)))

    summary = bounded_pressure_summary(health)

    assert "actionable due **353**" in summary
    assert "new/retry **11**" in summary
    assert "confirmed no-price retry **0**" in summary
    assert "page recovery **23**" in summary
    assert "scheduled rechecks **319**" in summary
    assert "actively verifying **2**" in summary
    assert "terminal" not in summary
    assert "total" not in summary


def test_page_recovery_reason_sample_is_bounded_and_index_seekable() -> None:
    conn = PageReasonConnection(
        [
            {"last_error": "retry_kind=page_pacing_deferred first"},
            {"last_error": "retry_kind=page_http_403 second"},
            {"last_error": "retry_kind=page_pacing_deferred third"},
            {"last_error": "retry_kind=page_http_410 legacy"},
            {"last_error": "retry_kind=page_pacing_deferred fourth"},
            {"last_error": "legacy row without token"},
        ]
    )

    sample = asyncio.run(
        load_due_walmart_page_recovery_reason_sample(
            conn,
            now_iso="2026-09-11T13:00:00+00:00",
            discovery_cutoff="2026-08-28T13:00:00+00:00",
            cap=999,
        )
    )

    assert sample.sampled == 6
    assert sample.reasons == (
        ("page_pacing_deferred", 3),
        ("page_http_403", 1),
        ("page_http_410", 1),
        ("unclassified", 1),
    )
    assert "status = 'page_retry'" in conn.sql
    assert "INDEXED BY idx_walmart_exact_detail_queue_status_due" in conn.sql
    assert "next_attempt_at <= ?" in conn.sql
    assert "last_seen_at >= ?" in conn.sql
    assert "lease_until IS NULL OR lease_until < ?" in conn.sql
    assert "ORDER BY next_attempt_at ASC" in conn.sql
    assert conn.params[-1] == 64


def test_page_recovery_reason_diagnostic_has_a_five_minute_cadence() -> None:
    previous = pressure_module._page_recovery_reason_next_at
    try:
        pressure_module._page_recovery_reason_next_at = 0.0
        assert _reserve_page_recovery_reason_diagnostic(now_monotonic=100.0) is True
        assert _reserve_page_recovery_reason_diagnostic(now_monotonic=399.0) is False
        assert _reserve_page_recovery_reason_diagnostic(now_monotonic=400.0) is True
    finally:
        pressure_module._page_recovery_reason_next_at = previous

    assert PAGE_RECOVERY_REASON_DIAGNOSTIC_INTERVAL_SECONDS == 5 * 60
    assert PAGE_RECOVERY_REASON_DIAGNOSTIC_MARKER == "page_recovery_reason_sample_v288"


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


def test_pressure_query_is_capped_and_index_seekable() -> None:
    assert PRESSURE.count("LIMIT ?") == 6
    assert "DEFAULT_PRESSURE_CAP = 600" in PRESSURE
    assert "FRESH_PRESSURE_CAP = 64" in PRESSURE
    assert "CONFIRMED_NO_PRICE_PRESSURE_CAP = DEFAULT_PRESSURE_CAP" in PRESSURE
    assert "RECOVERY_PRESSURE_CAP = 64" in PRESSURE
    assert "VERIFYING_PRESSURE_CAP = 64" in PRESSURE
    assert "status IN ('pending', 'retry', 'failed', 'verifying')" in PRESSURE
    assert "VERIFIED_MARKDOWN_RECHECK_STATUSES" in PRESSURE
    assert "VERIFIED_REFERENCE_RECHECK_STATUSES" in PRESSURE
    assert "VERIFIED_LOW_SIGNAL_RECHECK_STATUSES" in PRESSURE
    assert "verified_at IS NULL" in PRESSURE
    assert "lease_until >= ?" in PRESSURE
    assert "STATUS_DUE_INDEX" in PRESSURE
    assert "STATUS_VERIFIED_DUE_INDEX" in PRESSURE
    assert "INDEXED BY {STATUS_DUE_INDEX}" in PRESSURE
    assert "INDEXED BY {STATUS_VERIFIED_DUE_INDEX}" in PRESSURE
    assert "PAGE_RECOVERY_REASON_DIAGNOSTIC_MARKER" in PRESSURE
    assert "load_due_walmart_page_recovery_reason_sample" in PRESSURE
