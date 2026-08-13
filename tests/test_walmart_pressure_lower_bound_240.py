from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_exact_queue_pressure import bounded_pressure_summary


def test_saturated_page_recovery_pressure_is_labeled_as_lower_bound() -> None:
    health = WalmartExactQueueHealth(
        total=64,
        due_now=64,
        initial_due_now=0,
        recovery_due_now=64,
        recheck_due_now=0,
        verifying=0,
    )

    summary = bounded_pressure_summary(health)

    assert "actionable due **>=64**" in summary
    assert "page recovery **>=64**" in summary
    assert "new/retry **0**" in summary


def test_unsaturated_pressure_keeps_exact_measured_value() -> None:
    health = WalmartExactQueueHealth(
        total=23,
        due_now=23,
        initial_due_now=0,
        recovery_due_now=23,
        recheck_due_now=0,
        verifying=0,
    )

    summary = bounded_pressure_summary(health)

    assert "actionable due **23**" in summary
    assert "page recovery **23**" in summary
    assert ">=23" not in summary
