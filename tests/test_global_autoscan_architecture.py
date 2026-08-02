from __future__ import annotations

from pathlib import Path

from sniperplug.services.walmart_global_catalog_autoscan import (
    GlobalCatalogClaim,
    catalog_backpressure_reason,
)
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth


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


def test_global_runner_replaces_per_guild_scheduled_discovery() -> None:
    assert "Do not start the inherited per-guild scheduled route loop" in RUNNER
    assert "self.global_reconciliation_loop.start()" in RUNNER
    assert "self.auto_scan_loop.start()" not in RUNNER
    assert "_walmart_global_catalog_worker" in RUNNER
    assert "global_catalog_autoscan" in RUNNER
    assert "per_guild_discovery=false" in RUNNER


def test_global_cursor_advances_only_after_completed_batch() -> None:
    assert "claim_next_catalog_routes" in RUNNER
    assert "complete_catalog_claim" in RUNNER
    assert "release_catalog_claim" in RUNNER
    assert RUNNER.index("collect_verified_discount_cards_with_observed_memory") < RUNNER.index(
        "completed = await complete_catalog_claim"
    )
    assert "durable cursor was not advanced" in RUNNER


def test_exact_worker_fans_out_to_guilds_and_personal_dms() -> None:
    assert "fanout_recent_exact_walmart_deals" in RUNNER
    assert "maybe_post_public_deal_cards" in FANOUT
    assert "list_enabled_dm_deal_alert_preferences" in FANOUT
    assert "dm_receipt_exists" in FANOUT
    assert "max_alerts_per_day" in FANOUT
    assert "global_catalog_autoscan:exact_verified" in FANOUT


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
        due_now=100,
        identity_blocked=0,
        pending=400,
        verifying=10,
    )

    assert catalog_backpressure_reason(only_identity_blocks) is None
    assert catalog_backpressure_reason(actionable_backlog) is not None


def test_global_state_is_durable_and_not_wall_clock_rotation() -> None:
    assert "walmart_global_catalog_autoscan_state" in STATE
    assert "cursor_index" in STATE
    assert "claim_token" in STATE
    assert "lease_until" in STATE
    assert "time.time()" not in STATE
