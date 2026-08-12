from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from sniperplug.services.walmart_global_catalog_autoscan import (
    CLAIM_LEASE_SECONDS,
    STATE_KEY,
    STATE_TABLE,
    claim_next_catalog_routes,
    complete_catalog_claim,
    release_catalog_claim,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(
    encoding="utf-8"
)


class _Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    def require_conn(self) -> aiosqlite.Connection:
        return self._conn


def test_catalog_claim_lease_is_bounded_for_restart_recovery() -> None:
    assert CLAIM_LEASE_SECONDS == 3 * 60


def test_old_long_lease_is_recovered_after_new_age_ceiling_without_skipping() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = _Database(conn)
        started = datetime(2026, 8, 12, 23, 28, 30, tzinfo=timezone.utc)
        try:
            first = await claim_next_catalog_routes(
                db,
                route_count=2,
                now=started,
            )
            assert first is not None
            assert first.recovered_abandoned_claim is False

            # Reproduce the production upgrade shape: the previous version had
            # already persisted a 15-minute lease before the process died.
            await conn.execute(
                f"UPDATE {STATE_TABLE} SET lease_until = ? WHERE state_key = ?",
                ((started + timedelta(minutes=15)).isoformat(), STATE_KEY),
            )
            await conn.commit()

            blocked = await claim_next_catalog_routes(
                db,
                route_count=2,
                now=started + timedelta(seconds=CLAIM_LEASE_SECONDS - 1),
            )
            assert blocked is None

            recovered = await claim_next_catalog_routes(
                db,
                route_count=2,
                now=started + timedelta(seconds=CLAIM_LEASE_SECONDS + 1),
            )
            assert recovered is not None
            assert recovered.token != first.token
            assert recovered.start_index == first.start_index
            assert recovered.queries == first.queries
            assert recovered.recovered_abandoned_claim is True
            assert "recovered abandoned claim **yes**" in recovered.summary_line()

            # The dead process cannot later complete its obsolete token and
            # advance the cursor out from under the replacement worker.
            assert (
                await complete_catalog_claim(
                    db,
                    first,
                    now=started + timedelta(seconds=CLAIM_LEASE_SECONDS + 2),
                )
                is False
            )

            assert (
                await complete_catalog_claim(
                    db,
                    recovered,
                    now=started + timedelta(seconds=CLAIM_LEASE_SECONDS + 3),
                )
                is True
            )
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_failed_claim_release_does_not_advance_cursor() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = _Database(conn)
        started = datetime(2026, 8, 12, 23, 40, 0, tzinfo=timezone.utc)
        try:
            claim = await claim_next_catalog_routes(
                db,
                route_count=2,
                now=started,
            )
            assert claim is not None
            await release_catalog_claim(
                db,
                claim,
                error="CancelledError: catalog worker cancelled",
                now=started + timedelta(seconds=1),
            )
            replacement = await claim_next_catalog_routes(
                db,
                route_count=2,
                now=started + timedelta(seconds=2),
            )
            assert replacement is not None
            assert replacement.start_index == claim.start_index
            assert replacement.queries == claim.queries
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_runner_awaits_shutdown_and_releases_inflight_catalog_claim() -> None:
    assert "catalog_claim_lease_s=%s" in RUNNER
    assert "await asyncio.gather(*tasks_to_cancel, return_exceptions=True)" in RUNNER
    assert "Failed to release global catalog claim during cancellation" in RUNNER
    assert 'error="CancelledError: catalog worker cancelled"' in RUNNER
    assert "claim is not None and not claim_completed" in RUNNER
    assert "durable cursor was already advanced" in RUNNER
