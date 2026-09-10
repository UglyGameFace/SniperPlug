from __future__ import annotations

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_fresh_work_policy import (
    FRESH_DISCOVERY_PAUSE_THRESHOLD,
    catalog_backpressure_reason,
)


def test_production_shape_with_600_no_price_and_64_page_rows_does_not_pause_catalog() -> None:
    health = WalmartExactQueueHealth(
        due_now=664,
        initial_due_now=0,
        confirmed_no_price_due_now=600,
        recovery_due_now=64,
        recheck_due_now=0,
        verifying=0,
    )

    assert catalog_backpressure_reason(health) is None


def test_confirmed_no_price_wave_cannot_hide_real_fresh_backpressure() -> None:
    health = WalmartExactQueueHealth(
        due_now=612,
        initial_due_now=FRESH_DISCOVERY_PAUSE_THRESHOLD,
        confirmed_no_price_due_now=600,
        recovery_due_now=0,
        recheck_due_now=0,
        verifying=0,
    )

    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "fresh exact-detail backpressure" in reason
    assert "confirmed no-price retries **600**" in reason


def test_non_no_price_backlog_still_triggers_split_aware_emergency_stop() -> None:
    health = WalmartExactQueueHealth(
        due_now=1_200,
        initial_due_now=0,
        confirmed_no_price_due_now=600,
        recovery_due_now=0,
        recheck_due_now=600,
        verifying=0,
    )

    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "exact-detail emergency backpressure active" in reason
    assert "controllable backlog **600/600**" in reason
    assert "actionable backlog **1200**" in reason
    assert "confirmed no-price retries **600**" in reason


def test_legacy_unsplit_health_keeps_historical_total_backlog_stop() -> None:
    health = WalmartExactQueueHealth(
        due_now=450,
        initial_due_now=0,
        confirmed_no_price_due_now=0,
        recovery_due_now=0,
        recheck_due_now=0,
        verifying=0,
    )

    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "legacy unsplit health" in reason
