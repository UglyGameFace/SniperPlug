from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services.walmart_exact_queue_health import (
    WalmartExactQueueHealth,
    load_walmart_exact_queue_health,
)
from sniperplug.services.walmart_exact_queue_pressure import (
    bounded_pressure_summary,
    load_walmart_exact_queue_pressure,
)
from sniperplug.services.walmart_exact_runtime_schema import (
    ensure_exact_runtime_schema_once,
)
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE
from sniperplug.services.walmart_fresh_work_policy import (
    FRESH_DISCOVERY_PAUSE_THRESHOLD,
    catalog_backpressure_reason,
    queue_split_is_known,
    should_use_drain_mode,
)


CONFIRMED_NO_PRICE_ERROR = (
    "retry_kind=api_price_missing exact detail did not provide a numeric current price; "
    "recovery_kind=api_price_missing"
)
ORDINARY_RETRY_ERROR = "retry_kind=api_http_500 transient upstream failure"
ROOT = Path(__file__).resolve().parents[1]


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _seed_due_row(
    conn,
    *,
    item_id: str,
    status: str,
    error: str = "",
) -> None:
    now = datetime.now(timezone.utc)
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
            last_error
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            item_id,
            (now - timedelta(hours=8)).isoformat(),
            (now - timedelta(seconds=1)).isoformat(),
            status,
            (now - timedelta(hours=7)).isoformat(),
            (now - timedelta(minutes=1)).isoformat(),
            error,
        ),
    )


def test_real_queue_splits_confirmed_no_price_from_genuine_fresh_pressure() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        try:
            await ensure_exact_runtime_schema_once(db)
            await _seed_due_row(conn, item_id="930001", status="pending")
            await _seed_due_row(
                conn,
                item_id="930002",
                status="retry",
                error=ORDINARY_RETRY_ERROR,
            )
            for index in range(20):
                await _seed_due_row(
                    conn,
                    item_id=f"931{index:03d}",
                    status="retry",
                    error=CONFIRMED_NO_PRICE_ERROR,
                )
            await conn.commit()

            pressure = await load_walmart_exact_queue_pressure(db, cap=64)
            assert pressure.due_now == 22
            assert pressure.initial_due_now == 2
            assert pressure.confirmed_no_price_due_now == 20
            assert pressure.recovery_due_now == 0
            assert pressure.recheck_due_now == 0

            health = await load_walmart_exact_queue_health(db)
            assert health.due_now == 22
            assert health.initial_due_now == 2
            assert health.confirmed_no_price_due_now == 20
        finally:
            await conn.close()

    asyncio.run(run())


def test_catalog_pressure_loader_keeps_no_price_emergency_headroom() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        try:
            await ensure_exact_runtime_schema_once(db)
            for index in range(65):
                await _seed_due_row(
                    conn,
                    item_id=f"932{index:03d}",
                    status="retry",
                    error=CONFIRMED_NO_PRICE_ERROR,
                )
            await conn.commit()

            pressure = await load_walmart_exact_queue_pressure(db, cap=600)
            assert pressure.due_now == 65
            assert pressure.initial_due_now == 0
            assert pressure.confirmed_no_price_due_now == 65

            reason = catalog_backpressure_reason(
                pressure,
                emergency_total_limit=65,
            )
            assert reason is not None
            assert "emergency" in reason
            assert should_use_drain_mode(pressure) is False
        finally:
            await conn.close()

    asyncio.run(run())


def test_shortened_no_price_wake_stays_in_genuine_fresh_pressure() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        try:
            await ensure_exact_runtime_schema_once(db)
            await _seed_due_row(
                conn,
                item_id="930003",
                status="retry",
                error=CONFIRMED_NO_PRICE_ERROR,
            )
            now = datetime.now(timezone.utc)
            await conn.execute(
                f"""
                UPDATE {QUEUE_TABLE}
                SET last_attempt_at = ?, last_seen_at = ?, next_attempt_at = ?
                WHERE item_id = ?
                """,
                (
                    (now - timedelta(minutes=2)).isoformat(),
                    (now - timedelta(minutes=1)).isoformat(),
                    (now - timedelta(seconds=1)).isoformat(),
                    "930003",
                ),
            )
            await conn.commit()

            # A #277 fresh/material wake shortens the six-hour no-price schedule.
            # The old failure signature remains until exact re-verification, so
            # pressure classification must use the shortened schedule as the
            # signal that this row is genuinely fresh again.
            pressure = await load_walmart_exact_queue_pressure(db, cap=64)
            assert pressure.due_now == 1
            assert pressure.initial_due_now == 1
            assert pressure.confirmed_no_price_due_now == 0

            health = await load_walmart_exact_queue_health(db)
            assert health.due_now == 1
            assert health.initial_due_now == 1
            assert health.confirmed_no_price_due_now == 0
        finally:
            await conn.close()

    asyncio.run(run())


def test_confirmed_no_price_wave_does_not_pause_catalog_or_force_drain() -> None:
    health = WalmartExactQueueHealth(
        due_now=50,
        initial_due_now=0,
        confirmed_no_price_due_now=50,
        recovery_due_now=0,
        recheck_due_now=0,
        verifying=0,
    )

    assert queue_split_is_known(health) is True
    assert catalog_backpressure_reason(health) is None
    assert should_use_drain_mode(health) is False


def test_real_fresh_pressure_still_pauses_with_no_price_retries_present() -> None:
    health = WalmartExactQueueHealth(
        due_now=80,
        initial_due_now=FRESH_DISCOVERY_PAUSE_THRESHOLD,
        confirmed_no_price_due_now=68,
        recovery_due_now=0,
        recheck_due_now=0,
        verifying=0,
    )

    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "fresh/retry pressure" in reason
    assert f"confirmed no-price retries **68**" in reason


def test_no_price_lane_remains_part_of_total_emergency_pressure() -> None:
    health = WalmartExactQueueHealth(
        due_now=600,
        initial_due_now=0,
        confirmed_no_price_due_now=600,
        recovery_due_now=0,
        recheck_due_now=0,
        verifying=0,
    )

    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "emergency" in reason
    assert should_use_drain_mode(health) is False


def test_bounded_health_text_exposes_no_price_lane_for_live_acceptance() -> None:
    health = WalmartExactQueueHealth(
        due_now=606,
        initial_due_now=6,
        confirmed_no_price_due_now=600,
        recovery_due_now=0,
        recheck_due_now=0,
    )
    summary = bounded_pressure_summary(health)
    assert "confirmed no-price retry **>=600**" in summary

    source = (
        ROOT / "sniperplug/services/walmart_exact_queue_pressure.py"
    ).read_text(encoding="utf-8")
    assert "confirmed_no_price_due" in source
    assert "recovery_kind=api_price_missing" in source
    assert "CONFIRMED_NO_PRICE_FLOOR_SQL" in source
    assert "CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS" in source
    assert "CONFIRMED_NO_PRICE_PRESSURE_CAP = DEFAULT_PRESSURE_CAP" in source
