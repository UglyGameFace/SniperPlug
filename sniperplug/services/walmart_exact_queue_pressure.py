from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
)


DEFAULT_PRESSURE_CAP = 2_000


async def load_walmart_exact_queue_pressure(
    db: Any,
    *,
    cap: int = DEFAULT_PRESSURE_CAP,
) -> WalmartExactQueueHealth:
    """Load only bounded counts needed by scheduling and background logs.

    The complete owner-health query intentionally scans the queue to report every
    long-lived status. It is not appropriate before and after every exact cycle.
    This snapshot stops after ``cap`` matching rows in each actionable lane,
    which is enough for every drain/backpressure decision while protecting the
    serialized database worker even when production enters remote-only fallback.
    """

    if db is None:
        return WalmartExactQueueHealth()
    bounded_cap = max(1, int(cap))
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
            (SELECT COUNT(*) FROM recheck_due) AS recheck_due_now,
            (SELECT COUNT(*) FROM active_verifying) AS verifying
        """,
        (
            now_iso,
            discovery_cutoff,
            now_iso,
            bounded_cap,
            now_iso,
            discovery_cutoff,
            now_iso,
            bounded_cap,
            now_iso,
            bounded_cap,
        ),
    )
    row = await cursor.fetchone()
    fresh = _as_int(_row_get(row, "initial_due_now", 0))
    rechecks = _as_int(_row_get(row, "recheck_due_now", 1))
    verifying = _as_int(_row_get(row, "verifying", 2))
    due = fresh + rechecks
    return WalmartExactQueueHealth(
        total=due + verifying,
        due_now=due,
        initial_due_now=fresh,
        recheck_due_now=rechecks,
        verifying=verifying,
    )


def bounded_pressure_summary(health: WalmartExactQueueHealth) -> str:
    """Describe only values actually measured by the bounded snapshot."""

    return (
        "bounded queue pressure: "
        f"actionable due **{max(0, int(health.due_now or 0))}** "
        f"(new/retry **{max(0, int(health.initial_due_now or 0))}** • "
        f"scheduled rechecks **{max(0, int(health.recheck_due_now or 0))}**) • "
        f"actively verifying **{max(0, int(health.verifying or 0))}**"
    )


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
