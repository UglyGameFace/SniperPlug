from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_runtime import (
    TERMINAL_IDENTITY_REPROBE_DAYS,
    TERMINAL_NEXT_ATTEMPT,
    TerminalIdentityMaintenance,
    _ensure_terminal_identity_indexes,
)
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE


# Terminal rows are already excluded from ordinary claims, so maintenance does
# not need to mutate every eligible row at once. Bounding both writes prevents a
# 15-minute maintenance tick from creating a burst of fresh reprobes or holding
# the single Turso worker behind a large UPDATE.
TERMINAL_REARM_LIMIT = 4
TERMINAL_QUARANTINE_LIMIT = 64


async def maintain_terminal_identity_rows_limited(
    db: Any,
    *,
    now: datetime | None = None,
    rearm_limit: int = TERMINAL_REARM_LIMIT,
    quarantine_limit: int = TERMINAL_QUARANTINE_LIMIT,
) -> TerminalIdentityMaintenance:
    if db is None:
        return TerminalIdentityMaintenance()

    conn = db.require_conn()
    await _ensure_terminal_identity_indexes(conn)
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_utc = now_dt.astimezone(timezone.utc)
    now_iso = now_utc.isoformat()
    reprobe_cutoff = (
        now_utc - timedelta(days=TERMINAL_IDENTITY_REPROBE_DAYS)
    ).isoformat()

    rearm_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = 'pending',
            next_attempt_at = ?,
            lease_token = '',
            lease_until = NULL,
            last_error = CASE
                WHEN last_error = '' THEN 'scheduled weekly identity reprobe'
                ELSE last_error || ' | scheduled weekly identity reprobe'
            END
        WHERE item_id IN (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status IN ('incomplete_identity', 'identity_mismatch')
              AND last_attempt_at IS NOT NULL
              AND last_attempt_at <= ?
              AND last_seen_at > last_attempt_at
            ORDER BY last_attempt_at ASC, last_seen_at DESC
            LIMIT ?
        )
          AND status IN ('incomplete_identity', 'identity_mismatch')
          AND last_attempt_at IS NOT NULL
          AND last_attempt_at <= ?
          AND last_seen_at > last_attempt_at
        RETURNING item_id
        """,
        (
            now_iso,
            reprobe_cutoff,
            max(1, int(rearm_limit)),
            reprobe_cutoff,
        ),
    )
    rearmed_rows = await rearm_cursor.fetchall()

    quarantine_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET next_attempt_at = ?,
            lease_token = '',
            lease_until = NULL
        WHERE item_id IN (
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE status IN ('incomplete_identity', 'identity_mismatch')
              AND next_attempt_at <= ?
            ORDER BY next_attempt_at ASC, last_seen_at DESC
            LIMIT ?
        )
          AND status IN ('incomplete_identity', 'identity_mismatch')
          AND next_attempt_at <= ?
        RETURNING item_id
        """,
        (
            TERMINAL_NEXT_ATTEMPT,
            now_iso,
            max(1, int(quarantine_limit)),
            now_iso,
        ),
    )
    quarantined_rows = await quarantine_cursor.fetchall()

    if rearmed_rows or quarantined_rows:
        await conn.commit()
    return TerminalIdentityMaintenance(
        quarantined=len(quarantined_rows),
        rearmed=len(rearmed_rows),
    )
