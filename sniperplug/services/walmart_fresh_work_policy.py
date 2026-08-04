from __future__ import annotations

from typing import Any

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth


FRESH_DRAIN_THRESHOLD = 48
FRESH_DISCOVERY_PAUSE_THRESHOLD = 12
TOTAL_DRAIN_EMERGENCY_THRESHOLD = 1_200
TOTAL_DISCOVERY_EMERGENCY_THRESHOLD = 600
LEGACY_ACTIONABLE_THRESHOLD = 450


def queue_split_is_known(health: WalmartExactQueueHealth) -> bool:
    """Return whether health exposes first-time/retry versus recheck counts."""

    return bool(
        int(getattr(health, "initial_due_now", 0) or 0)
        or int(getattr(health, "recheck_due_now", 0) or 0)
        or int(getattr(health, "due_now", 0) or 0) == 0
    )


def should_use_drain_mode(
    health: WalmartExactQueueHealth,
    *,
    fresh_threshold: int = FRESH_DRAIN_THRESHOLD,
    emergency_total_threshold: int = TOTAL_DRAIN_EMERGENCY_THRESHOLD,
    legacy_threshold: int = LEGACY_ACTIONABLE_THRESHOLD,
) -> bool:
    """Use 24/4 only for substantial fresh pressure or a true emergency.

    Discovery pauses before fresh work becomes severe, so ordinary recovery can
    stay at the safer normal concurrency. Scheduled rechecks alone do not force
    aggressive drain mode until the backlog is genuinely extreme.
    """

    due_now = max(0, int(getattr(health, "due_now", 0) or 0))
    verifying = max(0, int(getattr(health, "verifying", 0) or 0))

    if not queue_split_is_known(health):
        return due_now + verifying >= max(1, int(legacy_threshold))

    fresh_due = max(0, int(getattr(health, "initial_due_now", 0) or 0))
    fresh_pressure = fresh_due + verifying
    total_pressure = due_now + verifying
    return bool(
        fresh_pressure >= max(1, int(fresh_threshold))
        or total_pressure >= max(1, int(emergency_total_threshold))
    )


def catalog_backpressure_reason(
    health: WalmartExactQueueHealth,
    *,
    fresh_limit: int = FRESH_DISCOVERY_PAUSE_THRESHOLD,
    emergency_total_limit: int = TOTAL_DISCOVERY_EMERGENCY_THRESHOLD,
    legacy_limit: int = LEGACY_ACTIONABLE_THRESHOLD,
) -> str | None:
    """Pause catalog intake before it can outrun the exact worker.

    A small amount of fresh/retry work is normal. Once twelve fresh rows are due,
    discovery yields until the exact worker catches up. Recheck-only maintenance
    remains allowed below the 600-row safety ceiling, while a larger total queue
    pauses intake to protect Discord responsiveness and database health.
    """

    due_now = max(0, int(getattr(health, "due_now", 0) or 0))
    verifying = max(0, int(getattr(health, "verifying", 0) or 0))
    identity_blocked = max(
        0,
        int(getattr(health, "identity_blocked", 0) or 0),
    )
    total_pressure = due_now + verifying

    if not queue_split_is_known(health):
        limit = max(25, int(legacy_limit))
        if total_pressure < limit:
            return None
        return (
            "exact-detail backpressure active (legacy unsplit health): "
            f"actionable backlog **{total_pressure}/{limit}** • "
            f"actionable due **{due_now}** • verifying **{verifying}** • "
            f"terminal identity blocks excluded **{identity_blocked}**"
        )

    fresh_due = max(0, int(getattr(health, "initial_due_now", 0) or 0))
    recheck_due = max(0, int(getattr(health, "recheck_due_now", 0) or 0))
    fresh_pressure = fresh_due + verifying
    fresh_limit_value = max(1, int(fresh_limit))
    emergency_limit_value = max(
        fresh_limit_value,
        int(emergency_total_limit),
    )

    if fresh_pressure >= fresh_limit_value:
        return (
            "fresh exact-detail backpressure active: "
            f"fresh/retry pressure **{fresh_pressure}/{fresh_limit_value}** • "
            f"new/retry due **{fresh_due}** • scheduled rechecks **{recheck_due}** • "
            f"verifying **{verifying}** • terminal identity blocks excluded "
            f"**{identity_blocked}**"
        )

    if total_pressure >= emergency_limit_value:
        return (
            "exact-detail emergency backpressure active: "
            f"actionable backlog **{total_pressure}/{emergency_limit_value}** • "
            f"new/retry due **{fresh_due}** • scheduled rechecks **{recheck_due}** • "
            f"verifying **{verifying}** • terminal identity blocks excluded "
            f"**{identity_blocked}**"
        )

    return None


def fresh_work_policy_summary(health: Any) -> str:
    """Compact diagnostics for tests and owner-facing health logs."""

    return (
        "fresh-work policy: "
        f"new/retry={max(0, int(getattr(health, 'initial_due_now', 0) or 0))} "
        f"rechecks={max(0, int(getattr(health, 'recheck_due_now', 0) or 0))} "
        f"verifying={max(0, int(getattr(health, 'verifying', 0) or 0))}"
    )
