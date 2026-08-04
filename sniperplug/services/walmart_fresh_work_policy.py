from __future__ import annotations

from typing import Any

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth


FRESH_DRAIN_THRESHOLD = 48
FRESH_DISCOVERY_PAUSE_THRESHOLD = 96
TOTAL_EMERGENCY_THRESHOLD = 1_800
LEGACY_ACTIONABLE_THRESHOLD = 450


def queue_split_is_known(health: WalmartExactQueueHealth) -> bool:
    """Return whether health exposes first-time/retry versus recheck counts.

    Older or synthetic health snapshots may only populate ``due_now``. Those
    snapshots retain the legacy conservative behavior instead of being treated
    as recheck-only work.
    """

    return bool(
        int(getattr(health, "initial_due_now", 0) or 0)
        or int(getattr(health, "recheck_due_now", 0) or 0)
        or int(getattr(health, "due_now", 0) or 0) == 0
    )


def should_use_drain_mode(
    health: WalmartExactQueueHealth,
    *,
    fresh_threshold: int = FRESH_DRAIN_THRESHOLD,
    emergency_total_threshold: int = TOTAL_EMERGENCY_THRESHOLD,
    legacy_threshold: int = LEGACY_ACTIONABLE_THRESHOLD,
) -> bool:
    """Reserve 24/4 drain mode for fresh/retry pressure or a true emergency.

    Scheduled exact-price rechecks are maintenance work. They must not by
    themselves trigger large drain batches that compete with new catalog
    discovery and Discord heartbeats. If a health snapshot does not expose the
    lane split, preserve the prior fail-closed total-backlog behavior.
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
    emergency_total_limit: int = TOTAL_EMERGENCY_THRESHOLD,
    legacy_limit: int = LEGACY_ACTIONABLE_THRESHOLD,
) -> str | None:
    """Pause discovery only for fresh-work pressure or a true total emergency.

    Rechecks remain visible and continue in the background, but a few hundred
    due rechecks no longer stop the catalog from finding newly listed or newly
    discounted products. This prevents the 450-row pause/resume sawtooth where
    old maintenance work starved fresh deal discovery.
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
    fresh_limit_value = max(24, int(fresh_limit))
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
            f"actionable backlog **{total_pressure}/{legacy_limit}** • "
            f"emergency total limit **{emergency_limit_value}** • "
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
