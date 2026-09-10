from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_drain import (
    VERIFIED_LOW_SIGNAL_RECHECK_STATUSES,
    VERIFIED_MARKDOWN_RECHECK_STATUSES,
    VERIFIED_REFERENCE_RECHECK_STATUSES,
    verified_recheck_cutoffs,
)
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_exact_verification_queue import (
    CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS,
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
)


# Catalog emergency backpressure only needs bounded scheduling pressure. Fresh
# work trips much earlier (12/48), and a single exact worker cannot legitimately
# hold hundreds of active leases. Confirmed dual-no-price retries that are still
# on their full #277 cooldown schedule are counted in their own bounded lane: they
# remain actionable and visible, but no longer masquerade as newly discovered/
# ordinary retry pressure whenever their six-hour timers mature. The catalog-
# facing default can still count that lane up to the 600-row emergency threshold;
# the exact worker's tighter cap=64 automatically limits the same query when it
# only needs drain-mode information.
DEFAULT_PRESSURE_CAP = 600
FRESH_PRESSURE_CAP = 64
CONFIRMED_NO_PRICE_PRESSURE_CAP = DEFAULT_PRESSURE_CAP
RECOVERY_PRESSURE_CAP = 64
VERIFYING_PRESSURE_CAP = 64
STATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"
STATUS_VERIFIED_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_verified_due"
CONFIRMED_NO_PRICE_FLOOR_SQL = (
    f"julianday(next_attempt_at) >= "
    f"julianday(last_attempt_at, '+{CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS} seconds')"
)


def _sql_statuses(statuses: tuple[str, ...]) -> str:
    return ", ".join(f"'{status}'" for status in statuses)


async def load_walmart_exact_queue_pressure(
    db: Any,
    *,
    cap: int = DEFAULT_PRESSURE_CAP,
) -> WalmartExactQueueHealth:
    """Load policy-bounded scheduling counts through selective queue indexes.

    Turso charges rows scanned, not rows returned. A LIMIT therefore does not
    protect quota when SQLite has to walk thousands of legacy rows before it
    finds work that also satisfies the verified-age floor. The mature queue can
    contain rows whose old ``next_attempt_at`` is already due while their newer
    ``verified_at`` correctly keeps them below the 24h/72h/7d safety cadence.

    Fresh/pending/ordinary retry, confirmed dual-no-price retry, page recovery,
    and active-lease lanes seek the existing status/due index. Scheduled rechecks
    are split by verified-age cutoff and forced through the status/verified/due
    index, so recent verified rows are skipped by an index range instead of
    scanned and post-filtered every 20 seconds. Missing ``verified_at`` rows remain
    recoverable through a narrow NULL seek.

    The confirmed no-price split is scheduling-only. A dual-no-price retry enters
    that low-signal lane only while its ``next_attempt_at`` still reflects at
    least the full #277 cooldown from ``last_attempt_at``. #277 intentionally
    preserves that schedule across weak/cosmetic rediscovery, but shortens it when
    a complete fresh snapshot or material apparent current/reference change wakes
    the row. A shortened schedule therefore remains in ``initial_due_now`` and is
    treated as genuine fresh pressure without guessing from catalog timestamps.

    ``cap`` remains meaningful for total emergency pressure. With the default
    600-row catalog snapshot, the confirmed-no-price lane can contribute all the
    way to that emergency stop. Callers such as the exact worker may pass a lower
    cap (currently 64), which bounds every lane to the amount that caller needs.
    """

    if db is None:
        return WalmartExactQueueHealth()
    bounded_cap = max(1, int(cap))
    fresh_cap = min(bounded_cap, FRESH_PRESSURE_CAP)
    no_price_cap = min(bounded_cap, CONFIRMED_NO_PRICE_PRESSURE_CAP)
    recovery_cap = min(bounded_cap, RECOVERY_PRESSURE_CAP)
    recheck_cap = bounded_cap
    verifying_cap = min(bounded_cap, VERIFYING_PRESSURE_CAP)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    discovery_cutoff = (
        now - timedelta(days=QUEUE_RETENTION_DAYS)
    ).isoformat()
    (
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
    ) = verified_recheck_cutoffs(now)
    markdown_statuses = _sql_statuses(VERIFIED_MARKDOWN_RECHECK_STATUSES)
    reference_statuses = _sql_statuses(VERIFIED_REFERENCE_RECHECK_STATUSES)
    low_signal_statuses = _sql_statuses(VERIFIED_LOW_SIGNAL_RECHECK_STATUSES)
    all_verified_statuses = _sql_statuses(
        (
            *VERIFIED_MARKDOWN_RECHECK_STATUSES,
            *VERIFIED_REFERENCE_RECHECK_STATUSES,
            *VERIFIED_LOW_SIGNAL_RECHECK_STATUSES,
        )
    )
    cursor = await conn.execute(
        f"""
        WITH fresh_due AS (
            SELECT item_id
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
            WHERE status IN ('pending', 'retry', 'failed', 'verifying')
              AND NOT (
                  status = 'retry'
                  AND instr(lower(last_error), 'retry_kind=api_price_missing') > 0
                  AND instr(lower(last_error), 'recovery_kind=api_price_missing') > 0
                  AND last_attempt_at IS NOT NULL
                  AND {CONFIRMED_NO_PRICE_FLOOR_SQL}
              )
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            LIMIT ?
        ),
        confirmed_no_price_due AS (
            SELECT item_id
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
            WHERE status = 'retry'
              AND instr(lower(last_error), 'retry_kind=api_price_missing') > 0
              AND instr(lower(last_error), 'recovery_kind=api_price_missing') > 0
              AND last_attempt_at IS NOT NULL
              AND {CONFIRMED_NO_PRICE_FLOOR_SQL}
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            LIMIT ?
        ),
        recovery_due AS (
            SELECT item_id
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
            WHERE status = 'page_retry'
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            LIMIT ?
        ),
        recheck_due AS (
            SELECT item_id
            FROM (
                SELECT item_id
                FROM {QUEUE_TABLE} INDEXED BY {STATUS_VERIFIED_DUE_INDEX}
                WHERE status IN ({markdown_statuses})
                  AND verified_at <= ?
                  AND next_attempt_at <= ?
                  AND last_seen_at >= ?
                  AND (lease_until IS NULL OR lease_until < ?)
                UNION ALL
                SELECT item_id
                FROM {QUEUE_TABLE} INDEXED BY {STATUS_VERIFIED_DUE_INDEX}
                WHERE status IN ({reference_statuses})
                  AND verified_at <= ?
                  AND next_attempt_at <= ?
                  AND last_seen_at >= ?
                  AND (lease_until IS NULL OR lease_until < ?)
                UNION ALL
                SELECT item_id
                FROM {QUEUE_TABLE} INDEXED BY {STATUS_VERIFIED_DUE_INDEX}
                WHERE status IN ({low_signal_statuses})
                  AND verified_at <= ?
                  AND next_attempt_at <= ?
                  AND last_seen_at >= ?
                  AND (lease_until IS NULL OR lease_until < ?)
                UNION ALL
                SELECT item_id
                FROM {QUEUE_TABLE} INDEXED BY {STATUS_VERIFIED_DUE_INDEX}
                WHERE status IN ({all_verified_statuses})
                  AND verified_at IS NULL
                  AND next_attempt_at <= ?
                  AND last_seen_at >= ?
                  AND (lease_until IS NULL OR lease_until < ?)
            )
            LIMIT ?
        ),
        active_verifying AS (
            SELECT item_id
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
            WHERE status = 'verifying'
              AND lease_until IS NOT NULL
              AND lease_until >= ?
            LIMIT ?
        )
        SELECT
            (SELECT COUNT(*) FROM fresh_due) AS initial_due_now,
            (SELECT COUNT(*) FROM confirmed_no_price_due) AS confirmed_no_price_due_now,
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
            no_price_cap,
            now_iso,
            discovery_cutoff,
            now_iso,
            recovery_cap,
            markdown_recheck_cutoff,
            now_iso,
            discovery_cutoff,
            now_iso,
            reference_recheck_cutoff,
            now_iso,
            discovery_cutoff,
            now_iso,
            low_signal_recheck_cutoff,
            now_iso,
            discovery_cutoff,
            now_iso,
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
    no_price = _as_int(_row_get(row, "confirmed_no_price_due_now", 1))
    recovery = _as_int(_row_get(row, "recovery_due_now", 2))
    rechecks = _as_int(_row_get(row, "recheck_due_now", 3))
    verifying = _as_int(_row_get(row, "verifying", 4))
    due = fresh + no_price + recovery + rechecks
    return WalmartExactQueueHealth(
        total=due + verifying,
        due_now=due,
        initial_due_now=fresh,
        confirmed_no_price_due_now=no_price,
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
    no_price_cap = min(bounded_cap, CONFIRMED_NO_PRICE_PRESSURE_CAP)
    recovery_cap = min(bounded_cap, RECOVERY_PRESSURE_CAP)
    recheck_cap = bounded_cap
    verifying_cap = min(bounded_cap, VERIFYING_PRESSURE_CAP)

    fresh = max(0, int(health.initial_due_now or 0))
    no_price = max(0, int(health.confirmed_no_price_due_now or 0))
    recovery = max(0, int(health.recovery_due_now or 0))
    rechecks = max(0, int(health.recheck_due_now or 0))
    verifying = max(0, int(health.verifying or 0))
    due = max(0, int(health.due_now or 0))

    due_is_lower_bound = (
        fresh >= fresh_cap
        or no_price >= no_price_cap
        or recovery >= recovery_cap
        or rechecks >= recheck_cap
    )
    return (
        "bounded queue pressure: "
        f"actionable due **{_bounded_value(due, lower_bound=due_is_lower_bound)}** "
        f"(new/retry **{_bounded_value(fresh, lower_bound=fresh >= fresh_cap)}** • "
        f"confirmed no-price retry **{_bounded_value(no_price, lower_bound=no_price >= no_price_cap)}** • "
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
