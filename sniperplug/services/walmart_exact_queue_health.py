from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


QUEUE_TABLE = "walmart_exact_detail_queue"


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

    def summary_line(self) -> str:
        return (
            "queue health: "
            f"total **{self.total}** • due now **{self.due_now}** • "
            f"delayed retries **{self.delayed_retries}** • "
            f"identity blocked **{self.identity_blocked}** • "
            f"verified **{self.verified}** • verifying **{self.verifying}** • "
            f"pending **{self.pending}** • unavailable **{self.unavailable}**"
        )


async def load_walmart_exact_queue_health(db: Any) -> WalmartExactQueueHealth:
    """Return full queue state instead of only rows currently due.

    The worker's historic `due/pending` number intentionally counts only rows
    eligible at that instant. This snapshot makes delayed retries and blocked
    identities visible so `0 due` can no longer be mistaken for `0 unfinished`.
    """

    if db is None:
        return WalmartExactQueueHealth()
    conn = db.require_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        cursor = await conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE
                    WHEN next_attempt_at <= ?
                     AND (lease_until IS NULL OR lease_until < ?)
                    THEN 1 ELSE 0 END) AS due_now,
                SUM(CASE
                    WHEN status IN ('retry', 'failed', 'incomplete_identity', 'identity_mismatch')
                     AND next_attempt_at > ?
                    THEN 1 ELSE 0 END) AS delayed_retries,
                SUM(CASE
                    WHEN status IN ('incomplete_identity', 'identity_mismatch')
                    THEN 1 ELSE 0 END) AS identity_blocked,
                SUM(CASE
                    WHEN status LIKE 'verified_%'
                    THEN 1 ELSE 0 END) AS verified,
                SUM(CASE WHEN status = 'verifying' THEN 1 ELSE 0 END) AS verifying,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'not_buyable' THEN 1 ELSE 0 END) AS unavailable
            FROM {QUEUE_TABLE}
            """,
            (now_iso, now_iso, now_iso),
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
