from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
)


# Catalog emergency backpressure only needs to know whether total actionable
# pressure has reached 600. The fresh-work policy trips much earlier (12/48),
# and a single exact worker cannot legitimately hold hundreds of active leases.
# Capping each lane at the amount its policy can actually use prevents a 20s
# worker from scanning thousands of queue rows simply to produce diagnostics.
DEFAULT_PRESSURE_CAP = 600
FRESH_PRESSURE_CAP = 64
RECOVERY_PRESSURE_CAP = 64
VERIFYING_PRESSURE_CAP = 64


async def load_walmart_exact_queue_pressure(
    db: Any,
    *,
    cap: int = DEFAULT_PRESSURE_CAP,
) -> WalmartExactQueueHealth:
    """Load bounded scheduling counts without repeatedly scanning the queue.

    ``cap`` controls the scheduled-recheck lane because total emergency
    backpressure can depend on it. Fresh/retry and active-lease lanes are capped
    more tightly at the maximum information current scheduling policy can use.
    Counts at a cap are intentionally lower bounds; callers use this snapshot for
    threshold decisions and bounded logging, never owner-facing exact totals.
    """

    if db is None:
        return WalmartExactQueueHealth()
    bounded_cap = max(1, int(cap))
    fresh_cap = min(bounded_cap, FRESH_PRESSURE_CAP)
    recovery_cap = min(bounded_cap, RECOVERY_PRESSURE_CAP)
    recheck_cap = bounded_cap
    verifying_cap = min(bounded_cap, VERIFYING_PRESSURE_CAP)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    discovery_cutoff = (
        now - timedelta(days=QUEUE_RETENTION_DAYS)
    ).isoformat()
    cursor = await conn.execute(
        f"""
        WITH fresh_due AS (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status IN ('pending', 'retry', 'failed', 'verifying')
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            LIMIT ?
        ),
        recovery_due AS (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status = 'page_retry'
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            LIMIT ?
        ),
        recheck_due AS (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status IN (
                'verified_markdown',
                'verified_under_threshold',
                'verified_no_reference',
                'verified_reference',
                'not_buyable'
            )
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            LIMIT ?
        ),
        active_verifying AS (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status = 'verifying'
              AND lease_until IS NOT NULL
              AND lease_until >= ?
            LIMIT ?
        )
        SELECT
            (SELECT COUNT(*) FROM fresh_due) AS initial_due_now,
            (SELECT COUNT(*) FROM recovery_due) AS recovery_due_now,
            (SELECT COUNT(*) FROM recheck_due) AS recheck_due_now,
            (SELECT COUNT(*) FROM active_verifying) AS verifying
        """,
        (
            now_iso,
            discovery_cutoff,
            now_iso,
            fresh_cap,
            now_iso,
            discovery_cutoff,
            now_iso,
            recovery_cap,
            now_iso,
            discovery_cutoff,
            now_iso,
            recheck_cap,
            now_iso,
            verifying_cap,
        ),
    )
    row = await cursor.fetchone()
    fresh = _as_int(_row_get(row, "initial_due_now", 0))
    recovery = _as_int(_row_get(row, "recovery_due_now", 1))
    rechecks = _as_int(_row_get(row, "recheck_due_now", 2))
    verifying = _as_int(_row_get(row, "verifying", 3))
    due = fresh + recovery + rechecks
    return WalmartExactQueueHealth(
        total=due + verifying,
        due_now=due,
        initial_due_now=fresh,
        recovery_due_now=recovery,
        recheck_due_now=rechecks,
        verifying=verifying,
    )


def bounded_pressure_summary(
    health: WalmartExactQueueHealth,
    *,
    cap: int = DEFAULT_PRESSURE_CAP,
) -> str:
    """Describe measured pressure and mark saturated lanes as lower bounds."""

    bounded_cap = max(1, int(cap))
    fresh_cap = min(bounded_cap, FRESH_PRESSURE_CAP)
    recovery_cap = min(bounded_cap, RECOVERY_PRESSURE_CAP)
    recheck_cap = bounded_cap
    verifying_cap = min(bounded_cap, VERIFYING_PRESSURE_CAP)

    fresh = max(0, int(health.initial_due_now or 0))
    recovery = max(0, int(health.recovery_due_now or 0))
    rechecks = max(0, int(health.recheck_due_now or 0))
    verifying = max(0, int(health.verifying or 0))
    due = max(0, int(health.due_now or 0))

    due_is_lower_bound = (
        fresh >= fresh_cap
        or recovery >= recovery_cap
        or rechecks >= recheck_cap
    )
    return (
        "bounded queue pressure: "
        f"actionable due **{_bounded_value(due, lower_bound=due_is_lower_bound)}** "
        f"(new/retry **{_bounded_value(fresh, lower_bound=fresh >= fresh_cap)}** • "
        f"page recovery **{_bounded_value(recovery, lower_bound=recovery >= recovery_cap)}** • "
        f"scheduled rechecks **{_bounded_value(rechecks, lower_bound=rechecks >= recheck_cap)}**) • "
        f"actively verifying **{_bounded_value(verifying, lower_bound=verifying >= verifying_cap)}**"
    )


def _bounded_value(value: int, *, lower_bound: bool) -> str:
    return f">={max(0, int(value))}" if lower_bound else str(max(0, int(value)))


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
