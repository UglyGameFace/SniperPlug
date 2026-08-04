from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.services.walmart_global_catalog_autoscan import GlobalCatalogClaim
from sniperplug.services.walmart_fresh_work_policy import catalog_backpressure_reason
from sniperplug.services.walmart_exact_queue_health import (
    WalmartExactQueueHealth,
    load_walmart_exact_queue_health,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(
    encoding="utf-8"
)
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
FANOUT = (ROOT / "sniperplug/services/walmart_global_deal_fanout.py").read_text(
    encoding="utf-8"
)
STATE = (ROOT / "sniperplug/services/walmart_global_catalog_autoscan.py").read_text(
    encoding="utf-8"
)
DM_STORE = (ROOT / "sniperplug/services/dm_deal_alerts.py").read_text(
    encoding="utf-8"
)


def test_global_runner_replaces_per_guild_scheduled_discovery() -> None:
    assert "Do not start the inherited per-guild scheduled route loop" in RUNNER
    assert "self.global_reconciliation_loop.start()" in RUNNER
    assert "self.auto_scan_loop.start()" not in RUNNER
    assert "_walmart_global_catalog_worker" in RUNNER
    assert "global_catalog_autoscan" in RUNNER
    assert "per_guild_discovery=false" in RUNNER


def test_global_cursor_advances_only_after_completed_discovery_enqueue() -> None:
    assert "claim_next_catalog_routes" in RUNNER
    assert "complete_catalog_claim" in RUNNER
    assert "release_catalog_claim" in RUNNER
    assert RUNNER.index("discover_walmart_catalog_candidates") < RUNNER.index(
        "completed = await complete_catalog_claim"
    )
    assert "foreground_exact_checks=0" in RUNNER
    assert "catalog_discovery_only=true" in RUNNER
    assert "durable cursor was not advanced" in RUNNER


def test_exact_worker_fans_out_to_guilds_and_personal_dms() -> None:
    assert "fanout_recent_exact_walmart_deals" in RUNNER
    assert "maybe_post_public_deal_cards" in FANOUT
    assert "list_enabled_dm_deal_alert_preferences" in FANOUT
    assert "dm_receipt_exists" in FANOUT
    assert "max_alerts_per_day" in FANOUT
    assert "global_catalog_autoscan:exact_verified" in FANOUT


def test_fanout_events_use_durable_cross_process_leases() -> None:
    assert "claim_token TEXT NOT NULL DEFAULT ''" in FANOUT
    assert "lease_until TEXT" in FANOUT
    assert "EVENT_LEASE_SECONDS" in FANOUT
    assert "async def _claim_pending_events" in FANOUT
    assert "AND (lease_until IS NULL OR lease_until <= ?)" in FANOUT
    assert "WHERE deal_key = ? AND claim_token = ?" in FANOUT
    assert "uuid.uuid4().hex" in FANOUT


def test_fanout_uses_watermark_and_durable_snapshots_without_starvation() -> None:
    assert "walmart_global_exact_deal_fanout_state" in FANOUT
    assert "last_verified_at" in FANOUT
    assert "last_item_id" in FANOUT
    assert "ORDER BY verified_at ASC, item_id ASC" in FANOUT
    assert "snapshot_json TEXT NOT NULL" in FANOUT
    assert "_candidate_from_snapshot" in FANOUT


def test_dm_schema_initialization_is_one_time_and_receipts_are_bounded() -> None:
    assert "_SCHEMA_READY = False" in DM_STORE
    assert "_SCHEMA_LOCK = asyncio.Lock()" in DM_STORE
    assert DM_STORE.count("if _SCHEMA_READY:") >= 2
    assert "RECEIPT_RETENTION_DAYS = 90" in DM_STORE
    assert "DELETE FROM {RECEIPTS_TABLE} WHERE delivered_at < ?" in DM_STORE


def test_bot_loads_global_runner_and_dm_command() -> None:
    assert "GlobalAutoScanRunnerCog" in BOT
    assert "DmDealAlertsCog" in BOT
    assert "ResilientAutoScanRunnerCog" not in BOT
    assert "await self.add_cog(DmDealAlertsCog(self))" in BOT
    assert "await self.add_cog(GlobalAutoScanRunnerCog(self))" in BOT


def test_catalog_claim_wraps_without_skipping() -> None:
    claim = GlobalCatalogClaim(
        token="x",
        start_index=8,
        queries=("a", "b", "c", "d"),
        total_routes=10,
        completed_routes_before=20,
        completed_passes_before=2,
    )

    assert claim.next_index == 2
    assert claim.wraps_catalog is True


def test_backpressure_ignores_terminal_identity_blocks() -> None:
    only_identity_blocks = WalmartExactQueueHealth(
        total=1000,
        due_now=0,
        identity_blocked=1000,
        pending=0,
        verifying=0,
    )
    actionable_backlog = WalmartExactQueueHealth(
        total=1000,
        due_now=440,
        identity_blocked=0,
        pending=400,
        verifying=10,
    )

    assert catalog_backpressure_reason(only_identity_blocks) is None
    assert catalog_backpressure_reason(actionable_backlog) is not None


def test_backpressure_does_not_subtract_terminal_blocks_twice() -> None:
    production_shape = WalmartExactQueueHealth(
        total=11372,
        due_now=2818,
        initial_due_now=172,
        recheck_due_now=2646,
        delayed_retries=69,
        identity_blocked=7591,
        verified=2632,
        verifying=0,
        pending=0,
        unavailable=1054,
        stale=0,
    )

    reason = catalog_backpressure_reason(production_shape)

    assert reason is not None
    assert "fresh/retry pressure **172/12**" in reason
    assert "terminal identity blocks excluded **7591**" in reason


def test_backpressure_counts_active_verification_without_double_counting_pending() -> None:
    below_limit = WalmartExactQueueHealth(
        due_now=0,
        initial_due_now=0,
        recheck_due_now=0,
        pending=440,
        verifying=11,
    )
    at_limit = WalmartExactQueueHealth(
        due_now=1,
        initial_due_now=1,
        recheck_due_now=0,
        pending=440,
        verifying=11,
    )

    assert catalog_backpressure_reason(below_limit) is None
    assert catalog_backpressure_reason(at_limit) is not None


class _QueueHealthCursor:
    async def fetchone(self):
        return {
            "total": 10,
            "due_now": 3,
            "initial_due_now": 1,
            "recheck_due_now": 2,
            "delayed_retries": 1,
            "identity_blocked": 2,
            "verified": 2,
            "verifying": 1,
            "pending": 2,
            "unavailable": 1,
            "stale": 0,
        }


class _QueueHealthConnection:
    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple[str, ...] = ()

    async def execute(self, sql, params):
        self.sql = str(sql)
        self.params = tuple(params)
        return _QueueHealthCursor()


class _QueueHealthDatabase:
    def __init__(self, conn: _QueueHealthConnection) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_health_counts_only_actively_leased_verifying_rows() -> None:
    conn = _QueueHealthConnection()

    health = asyncio.run(load_walmart_exact_queue_health(_QueueHealthDatabase(conn)))

    assert health.initial_due_now == 1
    assert health.recheck_due_now == 2
    assert health.verifying == 1
    assert "status = 'verifying'" in conn.sql
    assert "AND lease_until IS NOT NULL" in conn.sql
    assert "AND lease_until >= ?" in conn.sql
    assert "scheduled rechecks" in health.summary_line()
    assert len(conn.params) == 9
    assert all(value == conn.params[1] for value in conn.params[1:])


def test_global_state_is_durable_and_not_wall_clock_rotation() -> None:
    assert "walmart_global_catalog_autoscan_state" in STATE
    assert "cursor_index" in STATE
    assert "claim_token" in STATE
    assert "lease_until" in STATE
    assert "time.time()" not in STATE
