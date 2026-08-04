from __future__ import annotations

from pathlib import Path

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_fresh_work_policy import (
    FRESH_DISCOVERY_PAUSE_THRESHOLD,
    FRESH_DRAIN_THRESHOLD,
    TOTAL_DISCOVERY_EMERGENCY_THRESHOLD,
    TOTAL_DRAIN_EMERGENCY_THRESHOLD,
    catalog_backpressure_reason,
    should_use_drain_mode,
)


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(
    encoding="utf-8"
)
BULK_RUNTIME = (
    ROOT / "sniperplug/services/walmart_exact_queue_bulk_runtime.py"
).read_text(encoding="utf-8")


def test_recheck_only_backlog_does_not_starve_fresh_discovery() -> None:
    health = WalmartExactQueueHealth(
        due_now=451,
        initial_due_now=0,
        recheck_due_now=451,
        verifying=0,
    )

    assert catalog_backpressure_reason(health) is None
    assert should_use_drain_mode(health) is False


def test_fresh_work_pauses_catalog_before_aggressive_drain() -> None:
    pause_health = WalmartExactQueueHealth(
        due_now=FRESH_DISCOVERY_PAUSE_THRESHOLD,
        initial_due_now=FRESH_DISCOVERY_PAUSE_THRESHOLD,
        recheck_due_now=0,
        verifying=0,
    )
    drain_health = WalmartExactQueueHealth(
        due_now=FRESH_DRAIN_THRESHOLD,
        initial_due_now=FRESH_DRAIN_THRESHOLD,
        recheck_due_now=0,
        verifying=0,
    )

    reason = catalog_backpressure_reason(pause_health)
    assert reason is not None
    assert "fresh/retry pressure" in reason
    assert "scheduled rechecks **0**" in reason
    assert should_use_drain_mode(pause_health) is False
    assert should_use_drain_mode(drain_health) is True


def test_total_recheck_backlog_pauses_catalog_but_never_forces_drain() -> None:
    pause_health = WalmartExactQueueHealth(
        due_now=TOTAL_DISCOVERY_EMERGENCY_THRESHOLD,
        initial_due_now=0,
        recheck_due_now=TOTAL_DISCOVERY_EMERGENCY_THRESHOLD,
        verifying=0,
    )
    extreme_health = WalmartExactQueueHealth(
        due_now=TOTAL_DRAIN_EMERGENCY_THRESHOLD * 2,
        initial_due_now=0,
        recheck_due_now=TOTAL_DRAIN_EMERGENCY_THRESHOLD * 2,
        verifying=24,
    )

    reason = catalog_backpressure_reason(pause_health)
    assert reason is not None
    assert "emergency" in reason
    assert str(TOTAL_DISCOVERY_EMERGENCY_THRESHOLD) in reason
    assert should_use_drain_mode(pause_health) is False
    assert should_use_drain_mode(extreme_health) is False


def test_active_recheck_leases_are_not_misclassified_as_fresh_pressure() -> None:
    health = WalmartExactQueueHealth(
        due_now=1_500,
        initial_due_now=1,
        recheck_due_now=1_499,
        verifying=24,
    )

    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "emergency" in reason
    assert "fresh/retry pressure" not in reason
    assert should_use_drain_mode(health) is False


def test_legacy_unsplit_health_keeps_conservative_behavior() -> None:
    below = WalmartExactQueueHealth(due_now=449)
    at_limit = WalmartExactQueueHealth(due_now=450)

    assert should_use_drain_mode(below) is False
    assert catalog_backpressure_reason(below) is None
    assert should_use_drain_mode(at_limit) is True
    assert catalog_backpressure_reason(at_limit) is not None


def test_production_worker_uses_fresh_work_policy() -> None:
    assert "walmart_fresh_work_policy" in RUNNER
    assert "fresh_work_priority=true" in RUNNER
    assert "catalog_discovery_only=true" in RUNNER
    assert "bounded_claim_steps=true" in RUNNER
    assert "scheduled_rechecks_never_drain=true" in RUNNER
    assert "atomic_exact_claim=true" not in RUNNER
    assert "load_walmart_exact_queue_pressure" in RUNNER
    assert "catalog_backpressure_reason(queue_pressure)" in RUNNER
    assert "background_full_queue_scans=false" in RUNNER
    assert "load_walmart_exact_queue_pressure" in BULK_RUNTIME
    assert "should_use_drain_mode(pressure_before)" in BULK_RUNTIME
    assert "DRAIN_ACTIONABLE_THRESHOLD" not in BULK_RUNTIME
