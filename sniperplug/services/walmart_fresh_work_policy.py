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
    """Return whether health exposes the scheduler's actionable lane split."""

    return bool(
        int(getattr(health, "initial_due_now", 0) or 0)
        or int(getattr(health, "confirmed_no_price_due_now", 0) or 0)
        or int(getattr(health, "recovery_due_now", 0) or 0)
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
    """Use 24/4 only for substantial genuinely fresh/ordinary retry pressure.

    Scheduled rechecks and confirmed dual-no-price retries must never force
    aggressive concurrency. The latter have already completed both official
    exact price paths and are intentionally kept retryable on the #277 six-hour
    cadence. Treating a mature wave of those known low-signal retries as fresh
    intake recreates the very backlog #277 was designed to cool.

    ``emergency_total_threshold`` remains in the signature for compatibility;
    split-aware health intentionally ignores it for drain mode. Catalog intake
    still has a separate emergency stop through ``catalog_backpressure_reason``.
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
    """Pause catalog intake for pressure that catalog intake can actually worsen.

    A small amount of genuinely fresh/pending/ordinary retry work is normal.
    Once twelve such rows are due, discovery yields until the exact worker catches
    up. Confirmed dual-no-price retries are excluded from that fresh lane because
    both official exact price paths already proved no usable current price and
    #277 deliberately put them on a six-hour retry cadence.

    Mature confirmed no-price rows are also excluded from the split-aware 600-row
    emergency stop. Production after #281 proved why: a six-hour wave can remain
    at the bounded 600-row diagnostic cap while page recovery is intentionally
    paced, permanently freezing catalog discovery even when new/ordinary work is
    zero. Pausing discovery cannot make that mature low-signal lane drain faster.
    The lane remains fully visible, fully retryable, and eligible to fill unused
    exact-worker capacity; it simply cannot veto new catalog intake by itself.

    Page recovery, scheduled rechecks, and active leases still count toward the
    split-aware emergency stop. Legacy unsplit health preserves the historical
    total-backlog behavior because it cannot safely identify the no-price lane.
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
    no_price_due = max(
        0,
        int(getattr(health, "confirmed_no_price_due_now", 0) or 0),
    )
    recovery_due = max(0, int(getattr(health, "recovery_due_now", 0) or 0))
    recheck_due = max(0, int(getattr(health, "recheck_due_now", 0) or 0))
    fresh_limit_value = max(1, int(fresh_limit))
    emergency_limit_value = max(
        fresh_limit_value,
        int(emergency_total_limit),
    )
    emergency_pressure = fresh_due + recovery_due + recheck_due + verifying

    if fresh_due >= fresh_limit_value:
        return (
            "fresh exact-detail backpressure active: "
            f"fresh/retry pressure **{fresh_due}/{fresh_limit_value}** • "
            f"new/ordinary-retry due **{fresh_due}** • "
            f"confirmed no-price retries **{no_price_due}** • "
            f"page recovery **{recovery_due}** • scheduled rechecks **{recheck_due}** • "
            f"verifying **{verifying}** • terminal identity blocks excluded "
            f"**{identity_blocked}**"
        )

    if emergency_pressure >= emergency_limit_value:
        return (
            "exact-detail emergency backpressure active: "
            f"controllable backlog **{emergency_pressure}/{emergency_limit_value}** • "
            f"actionable backlog **{total_pressure}** • "
            f"new/ordinary-retry due **{fresh_due}** • "
            f"confirmed no-price retries **{no_price_due}** • "
            f"page recovery **{recovery_due}** • scheduled rechecks **{recheck_due}** • "
            f"verifying **{verifying}** • terminal identity blocks excluded "
            f"**{identity_blocked}**"
        )

    return None


def fresh_work_policy_summary(health: Any) -> str:
    """Compact diagnostics for tests and owner-facing health logs."""

    return (
        "fresh-work policy: "
        f"new/ordinary_retry={max(0, int(getattr(health, 'initial_due_now', 0) or 0))} "
        f"confirmed_no_price={max(0, int(getattr(health, 'confirmed_no_price_due_now', 0) or 0))} "
        f"page_recovery={max(0, int(getattr(health, 'recovery_due_now', 0) or 0))} "
        f"rechecks={max(0, int(getattr(health, 'recheck_due_now', 0) or 0))} "
        f"verifying={max(0, int(getattr(health, 'verifying', 0) or 0))}"
    )