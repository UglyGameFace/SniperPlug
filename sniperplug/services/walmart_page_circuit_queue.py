from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
)


# Production can hold far more page-recovery rows than the 64-row bounded
# health snapshot shows. During an active Walmart page challenge circuit those
# rows cannot use HTML at all, so repeatedly leasing them only creates
# page_circuit_open failures and needless Turso writes. Sweep a bounded slice
# forward to the circuit close time on each 20-second worker cycle. Fresh
# catalog rediscovery can still rearm a row to pending immediately.
PAGE_CIRCUIT_DEFER_LIMIT = 64
PAGE_CIRCUIT_RETRY_GRACE_SECONDS = 15


async def defer_due_page_recovery_for_open_circuit(
    conn: Any,
    *,
    now: datetime,
    remaining_seconds: int,
    limit: int = PAGE_CIRCUIT_DEFER_LIMIT,
) -> int:
    """Move due page-recovery rows past the active HTML challenge cooldown.

    This is scheduling only. It creates no proof and does not alter seller,
    offer, price, shipping, threshold, category, DM, or dedupe decisions.
    Rows rescued by a new safe catalog snapshot are changed to ``pending`` by
    the catalog enqueue path and therefore remain immediately claimable.
    """

    remaining = max(0, int(remaining_seconds or 0))
    if remaining <= 0:
        return 0

    now_utc = now.astimezone(timezone.utc)
    now_iso = now_utc.isoformat()
    discovery_cutoff = (
        now_utc - timedelta(days=QUEUE_RETENTION_DAYS)
    ).isoformat()
    defer_until = (
        now_utc
        + timedelta(
            seconds=max(
                PAGE_CIRCUIT_RETRY_GRACE_SECONDS,
                remaining + PAGE_CIRCUIT_RETRY_GRACE_SECONDS,
            )
        )
    ).isoformat()
    bounded_limit = max(1, int(limit))

    cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET next_attempt_at = ?,
            lease_token = '',
            lease_until = NULL
        WHERE item_id IN (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status = 'page_retry'
              AND next_attempt_at <= ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            ORDER BY priority_score DESC, last_seen_at DESC
            LIMIT ?
        )
          AND status = 'page_retry'
          AND (lease_until IS NULL OR lease_until < ?)
        RETURNING item_id
        """,
        (
            defer_until,
            now_iso,
            discovery_cutoff,
            now_iso,
            bounded_limit,
            now_iso,
        ),
    )
    rows = await cursor.fetchall()
    if rows:
        await conn.commit()
    return len(rows)
