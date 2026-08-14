from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_drain import (
    VERIFIED_LOW_SIGNAL_RECHECK_STATUSES,
    VERIFIED_MARKDOWN_RECHECK_STATUSES,
    VERIFIED_REFERENCE_RECHECK_STATUSES,
    verified_recheck_cutoffs,
)
from sniperplug.services.walmart_exact_verification_queue import QUEUE_RETENTION_DAYS


QUEUE_TABLE = "walmart_exact_detail_queue"
TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")
INITIAL_WORK_STATUSES = ("pending", "retry", "failed", "verifying")
RECOVERY_RETRY_STATUSES = ("page_retry",)
RECHECK_STATUSES = (
    "verified_markdown",
    "verified_under_threshold",
    "verified_no_reference",
    "verified_reference",
    "not_buyable",
)


def _sql_statuses(statuses: tuple[str, ...]) -> str:
    return ", ".join(f"'{status}'" for status in statuses)


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class WalmartExactQueueHealth:
    total: int = 0
    due_now: int = 0
    initial_due_now: int = 0
    recovery_due_now: int = 0
    recheck_due_now: int = 0
    delayed_retries: int = 0
    delayed_recovery_retries: int = 0
    identity_blocked: int = 0
    verified: int = 0
    verifying: int = 0
    pending: int = 0
    unavailable: int = 0
    stale: int = 0

    def summary_line(self) -> str:
        return (
            "queue health: "
            f"total **{self.total}** • actionable due now **{self.due_now}** "
            f"(new/retry **{self.initial_due_now}** • page recovery **{self.recovery_due_now}** • "
            f"scheduled rechecks **{self.recheck_due_now}**) • "
            f"delayed transient retries **{self.delayed_retries}** "
            f"(page recovery **{self.delayed_recovery_retries}**) • "
            f"identity unavailable / safely blocked **{self.identity_blocked}** (terminal) • "
            f"verified **{self.verified}** • actively verifying **{self.verifying}** • "
            f"pending **{self.pending}** • unavailable **{self.unavailable}** • "
            f"stale/unclaimable **{self.stale}**"
        )


async def load_walmart_exact_queue_health(db: Any) -> WalmartExactQueueHealth:
    """Return queue state using the same retention and lease rules as the worker.

    ``due_now`` includes both first-time/retry work and truly claimable scheduled
    exact rechecks. Legacy early timestamps are ignored until each status reaches
    its shared fallback age: one day for verified markdowns, three days for
    reference/under-threshold states, and seven days for no-reference/unavailable
    states. Material catalog changes are ``pending`` and remain immediately
    actionable. Terminal identity failures stay fail-closed and are never
    actionable. ``verifying`` counts only actively leased rows; expired leases
    return to the appropriate due bucket.
    """

    if db is None:
        return WalmartExactQueueHealth()
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    discovery_cutoff = (now - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    (
        markdown_recheck_cutoff,
        reference_recheck_cutoff,
        low_signal_recheck_cutoff,
    ) = verified_recheck_cutoffs(now)
    markdown_statuses = _sql_statuses(VERIFIED_MARKDOWN_RECHECK_STATUSES)
    reference_statuses = _sql_statuses(VERIFIED_REFERENCE_RECHECK_STATUSES)
    low_signal_statuses = _sql_statuses(VERIFIED_LOW_SIGNAL_RECHECK_STATUSES)
    reference_cutoff_sql = _sql_literal(reference_recheck_cutoff)
    low_signal_cutoff_sql = _sql_literal(low_signal_recheck_cutoff)
    verified_guard = f"""
        (
            verified_at IS NULL
            OR (status IN ({markdown_statuses}) AND verified_at <= ?)
            OR (status IN ({reference_statuses}) AND verified_at <= {reference_cutoff_sql})
            OR (status IN ({low_signal_statuses}) AND verified_at <= {low_signal_cutoff_sql})
        )
    """
    try:
        cursor = await conn.execute(
            f"""
            WITH queue_state AS (
                SELECT *,
                    CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END AS is_recent
                FROM {QUEUE_TABLE}
            )
            SELECT
                COUNT(*) AS total,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status NOT IN ('incomplete_identity', 'identity_mismatch')
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                     AND (
                         status NOT IN (
                             'verified_markdown',
                             'verified_under_threshold',
                             'verified_no_reference',
                             'verified_reference',
                             'not_buyable'
                         )
                         OR {verified_guard}
                     )
                    THEN 1 ELSE 0 END) AS due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('pending', 'retry', 'failed', 'verifying')
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                    THEN 1 ELSE 0 END) AS initial_due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status = 'page_retry'
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                    THEN 1 ELSE 0 END) AS recovery_due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN (
                         'verified_markdown',
                         'verified_under_threshold',
                         'verified_no_reference',
                         'verified_reference',
                         'not_buyable'
                     )
                     AND next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                     AND {verified_guard}
                    THEN 1 ELSE 0 END) AS recheck_due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('retry', 'failed', 'page_retry')
                     AND next_attempt_at > ?
                    THEN 1 ELSE 0 END) AS delayed_retries,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status = 'page_retry'
                     AND next_attempt_at > ?
                    THEN 1 ELSE 0 END) AS delayed_recovery_retries,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('incomplete_identity', 'identity_mismatch')
                    THEN 1 ELSE 0 END) AS identity_blocked,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status LIKE 'verified_%'
                    THEN 1 ELSE 0 END) AS verified,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status = 'verifying'
                     AND lease_until IS NOT NULL
                     AND lease_until >= ?
                    THEN 1 ELSE 0 END) AS verifying,
                SUM(CASE
                    WHEN is_recent = 1 AND status = 'pending'
                    THEN 1 ELSE 0 END) AS pending,
                SUM(CASE
                    WHEN is_recent = 1 AND status = 'not_buyable'
                    THEN 1 ELSE 0 END) AS unavailable,
                SUM(CASE WHEN is_recent = 0 THEN 1 ELSE 0 END) AS stale
            FROM queue_state
            """,
            (
                discovery_cutoff,
                now_iso,
                now_iso,
                markdown_recheck_cutoff,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
                markdown_recheck_cutoff,
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        row = await cursor.fetchone()
    except Exception:
        return WalmartExactQueueHealth()

    return WalmartExactQueueHealth(
        total=_as_int(_row_get(row, "total", 0)),
        due_now=_as_int(_row_get(row, "due_now", 1)),
        initial_due_now=_as_int(_row_get(row, "initial_due_now", 2)),
        recovery_due_now=_as_int(_row_get(row, "recovery_due_now", 3)),
        recheck_due_now=_as_int(_row_get(row, "recheck_due_now", 4)),
        delayed_retries=_as_int(_row_get(row, "delayed_retries", 5)),
        delayed_recovery_retries=_as_int(_row_get(row, "delayed_recovery_retries", 6)),
        identity_blocked=_as_int(_row_get(row, "identity_blocked", 7)),
        verified=_as_int(_row_get(row, "verified", 8)),
        verifying=_as_int(_row_get(row, "verifying", 9)),
        pending=_as_int(_row_get(row, "pending", 10)),
        unavailable=_as_int(_row_get(row, "unavailable", 11)),
        stale=_as_int(_row_get(row, "stale", 12)),
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
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
