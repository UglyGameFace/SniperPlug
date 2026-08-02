from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_verification_queue import QUEUE_RETENTION_DAYS


QUEUE_TABLE = "walmart_exact_detail_queue"
TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")


@dataclass(frozen=True)
class WalmartExactQueueHealth:
    total: int = 0
    due_now: int = 0
    delayed_retries: int = 0
    identity_blocked: int = 0
    verified: int = 0
    verifying: int = 0
    pending: int = 0
    unavailable: int = 0
    stale: int = 0

    def summary_line(self) -> str:
        return (
            "queue health: "
            f"total **{self.total}** • actionable due now **{self.due_now}** • "
            f"delayed transient retries **{self.delayed_retries}** • "
            f"identity unavailable / safely blocked **{self.identity_blocked}** (terminal) • "
            f"verified **{self.verified}** • verifying **{self.verifying}** • "
            f"pending **{self.pending}** • unavailable **{self.unavailable}** • "
            f"stale/unclaimable **{self.stale}**"
        )


async def load_walmart_exact_queue_health(db: Any) -> WalmartExactQueueHealth:
    """Return queue state using the same retention window as the worker.

    ``due_now`` counts actionable rows only. Terminal seller/offer identity
    failures remain visible in their own bucket but never inflate backlog or
    catalog backpressure after their fail-closed verification attempt.
    """

    if db is None:
        return WalmartExactQueueHealth()
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    discovery_cutoff = (now - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
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
                    THEN 1 ELSE 0 END) AS due_now,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('retry', 'failed')
                     AND next_attempt_at > ?
                    THEN 1 ELSE 0 END) AS delayed_retries,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status IN ('incomplete_identity', 'identity_mismatch')
                    THEN 1 ELSE 0 END) AS identity_blocked,
                SUM(CASE
                    WHEN is_recent = 1
                     AND status LIKE 'verified_%'
                    THEN 1 ELSE 0 END) AS verified,
                SUM(CASE
                    WHEN is_recent = 1 AND status = 'verifying'
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
            (discovery_cutoff, now_iso, now_iso, now_iso),
        )
        row = await cursor.fetchone()
    except Exception:
        return WalmartExactQueueHealth()

    return WalmartExactQueueHealth(
        total=_as_int(_row_get(row, "total", 0)),
        due_now=_as_int(_row_get(row, "due_now", 1)),
        delayed_retries=_as_int(_row_get(row, "delayed_retries", 2)),
        identity_blocked=_as_int(_row_get(row, "identity_blocked", 3)),
        verified=_as_int(_row_get(row, "verified", 4)),
        verifying=_as_int(_row_get(row, "verifying", 5)),
        pending=_as_int(_row_get(row, "pending", 6)),
        unavailable=_as_int(_row_get(row, "unavailable", 7)),
        stale=_as_int(_row_get(row, "stale", 8)),
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
