from __future__ import annotations

from typing import Any

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth


FRESH_DRAIN_THRESHOLD = 48
FRESH_DISCOVERY_PAUSE_THRESHOLD = 12
TOTAL_DRAIN_EMERGENCY_THRESHOLD = 1_200
TOTAL_DISCOVERY_EMERGENCY_THRESHOLD = 600
# Compatibility export for diagnostics/tests. Split-aware production health no
# longer uses total recheck backlog to force aggressive drain concurrency.
TOTAL_EMERGENCY_THRESHOLD = TOTAL_DRAIN_EMERGENCY_THRESHOLD
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
    """Use 24/4 only for substantial unclaimed first-time/retry pressure.

    Scheduled rechecks must never force aggressive concurrency. Production logs
    proved that a large recheck-only backlog made the native Turso claim spend
    48-66 seconds leasing 24 rows, starving Discord's heartbeat. The dedicated
    worker is single-instance and non-overlapping, so actively leased rows also
    do not represent additional fresh pressure for the next batch decision.

    ``emergency_total_threshold`` remains in the signature for compatibility;
    split-aware health intentionally ignores it. Catalog intake still pauses on
    total pressure through ``catalog_backpressure_reason``.
    """

    due_now = max(0, int(getattr(health, "due_now", 0) or 0))
    verifying = max(0, int(getattr(health, "verifying", 0) or 0))

    if not queue_split_is_known(health):
        return due_now + verifying >= max(1, int(legacy_threshold))

    fresh_due = max(0, int(getattr(health, "initial_due_now", 0) or 0))
    return fresh_due >= max(1, int(fresh_threshold))


def catalog_backpressure_reason(
    health: WalmartExactQueueHealth,
    *,
    fresh_limit: int = FRESH_DISCOVERY_PAUSE_THRESHOLD,
    emergency_total_limit: int = TOTAL_DISCOVERY_EMERGENCY_THRESHOLD,
    legacy_limit: int = LEGACY_ACTIONABLE_THRESHOLD,
) -> str | None:
    """Pause catalog intake before it can outrun the exact worker.

    A small amount of fresh/retry work is normal. Once twelve unclaimed fresh
    rows are due, discovery yields until the exact worker catches up. Actively
    verifying rows are not added to fresh pressure because health cannot tell
    whether those leases came from fresh work or scheduled rechecks. Total
    pressure still includes active leases for the emergency intake stop.
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
    fresh_limit_value = max(1, int(fresh_limit))
    emergency_limit_value = max(
        fresh_limit_value,
        int(emergency_total_limit),
    )

    if fresh_due >= fresh_limit_value:
        return (
            "fresh exact-detail backpressure active: "
            f"fresh/retry pressure **{fresh_due}/{fresh_limit_value}** • "
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
