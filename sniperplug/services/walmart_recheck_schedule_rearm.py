from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_queue_drain import tiered_recheck_delay
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
    _row_get,
)


log = logging.getLogger("sniperplug.autoscan")
RECHECK_STATUSES = (
    "verified_markdown",
    "verified_under_threshold",
    "verified_no_reference",
    "verified_reference",
    "not_buyable",
)
DEFAULT_REARM_BATCH_SIZE = 200
_STATE_ATTR = "_sniperplug_walmart_recheck_rearm_complete"
_FALLBACK_COMPLETE: set[int] = set()


def _is_complete(conn: Any) -> bool:
    try:
        return bool(getattr(conn, _STATE_ATTR, False))
    except Exception:
        return id(conn) in _FALLBACK_COMPLETE


def _mark_complete(conn: Any) -> None:
    try:
        setattr(conn, _STATE_ATTR, True)
    except Exception:
        _FALLBACK_COMPLETE.add(id(conn))


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _discount_percent(status: str, exact_discount_bps: Any) -> float:
    if status != "verified_markdown":
        return 0.0
    try:
        return max(0.0, float(exact_discount_bps or 0) / 100.0)
    except (TypeError, ValueError):
        return 0.0


async def rearm_legacy_due_rechecks_bounded(
    conn: Any,
    *,
    now: datetime | None = None,
    limit: int = DEFAULT_REARM_BATCH_SIZE,
) -> int:
    """Repair old hourly schedules without postponing legitimately due checks.

    PR #208 lengthened future recheck cadence, but existing queue rows retained
    their old hourly ``next_attempt_at`` values. This function examines the most
    recently verified due rows first. A row is moved only when its own verified
    timestamp plus the current tiered delay is still in the future.

    One SELECT and one primary-key UPDATE are bounded to ``limit`` rows. When the
    newest due rows are already legitimately due, older rows cannot qualify for
    deferral either, so the process is marked complete for this connection.
    """

    if _is_complete(conn):
        return 0

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_iso = now_utc.isoformat()
    discovery_cutoff = (
        now_utc - timedelta(days=QUEUE_RETENTION_DAYS)
    ).isoformat()
    bounded_limit = max(1, min(250, int(limit)))
    placeholders = ", ".join("?" for _ in RECHECK_STATUSES)

    cursor = await conn.execute(
        f"""
        SELECT
            item_id, status, exact_discount_bps, verified_at,
            last_attempt_at, last_seen_at, next_attempt_at
        FROM {QUEUE_TABLE}
        WHERE status IN ({placeholders})
          AND last_seen_at >= ?
          AND next_attempt_at <= ?
          AND (lease_until IS NULL OR lease_until < ?)
        ORDER BY COALESCE(verified_at, last_attempt_at, last_seen_at) DESC
        LIMIT ?
        """,
        (*RECHECK_STATUSES, discovery_cutoff, now_iso, now_iso, bounded_limit),
    )
    rows = await cursor.fetchall()
    if not rows:
        _mark_complete(conn)
        return 0

    targets: list[tuple[str, str]] = []
    for row in rows:
        item_id = str(_row_get(row, "item_id", index=0) or "").strip()
        status = str(_row_get(row, "status", index=1) or "").strip()
        verified_at = _parse_datetime(
            _row_get(row, "verified_at", index=3)
            or _row_get(row, "last_attempt_at", index=4)
            or _row_get(row, "last_seen_at", index=5)
        )
        current_next = _parse_datetime(_row_get(row, "next_attempt_at", index=6))
        if not item_id or not status or verified_at is None or current_next is None:
            continue

        target = verified_at + tiered_recheck_delay(
            status,
            _discount_percent(status, _row_get(row, "exact_discount_bps", index=2)),
        )
        if target <= now_utc or target <= current_next:
            continue
        targets.append((item_id, target.isoformat()))

    if not targets:
        _mark_complete(conn)
        return 0

    case_parts = " ".join("WHEN ? THEN ?" for _ in targets)
    item_placeholders = ", ".join("?" for _ in targets)
    params: list[Any] = []
    for item_id, target_iso in targets:
        params.extend((item_id, target_iso))
    params.extend(item_id for item_id, _ in targets)
    params.extend((now_iso, now_iso))

    update_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET next_attempt_at = CASE item_id {case_parts} ELSE next_attempt_at END
        WHERE item_id IN ({item_placeholders})
          AND next_attempt_at <= ?
          AND (lease_until IS NULL OR lease_until < ?)
        RETURNING item_id
        """,
        tuple(params),
    )
    updated_rows = await update_cursor.fetchall()
    await conn.commit()
    updated = len(updated_rows)

    log.info(
        "Walmart legacy recheck schedule repair deferred=%s inspected=%s "
        "batch_limit=%s",
        updated,
        len(rows),
        bounded_limit,
    )
    if updated == 0 or len(rows) < bounded_limit:
        _mark_complete(conn)
    return updated
