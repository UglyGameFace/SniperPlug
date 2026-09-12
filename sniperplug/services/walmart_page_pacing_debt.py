from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sniperplug.services.walmart_exact_fetch_pacing import (
    PAGE_HTML_PROBE_INTERVAL_SECONDS,
)
from sniperplug.services.walmart_exact_queue_drain import STATUS_DUE_INDEX
from sniperplug.services.walmart_exact_verification_queue import (
    PAGE_RETRY_BASE_SECONDS,
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
    _compact_text,
)


log = logging.getLogger("sniperplug.autoscan")

PAGE_PACING_DEFERRED_TOKEN = "retry_kind=page_pacing_deferred"
PAGE_PACING_DEBT_MARKER = "page_pacing_debt_v290"
PAGE_PACING_SLOT_MARKER = "page_pacing_slot_v290"
PAGE_PACING_DEBT_BATCH_LIMIT = 200
_LEGACY_COMPLETE_ATTR = "_sniperplug_page_pacing_debt_v290_complete"


@dataclass(frozen=True)
class PagePacingScheduleResult:
    rows: int = 0
    sql_statements: int = 0
    first_slot: datetime | None = None
    last_slot: datetime | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (TypeError, IndexError):
            return None


def is_page_pacing_deferred(error: str) -> bool:
    return PAGE_PACING_DEFERRED_TOKEN in str(error or "").lower()


def _has_slot_marker(error: str) -> bool:
    return PAGE_PACING_SLOT_MARKER in str(error or "").lower()


def _mark_scheduled_error(error: str) -> str:
    text = str(error or "").strip()
    if _has_slot_marker(text):
        return _compact_text(text, 500)
    suffix = f" marker={PAGE_PACING_SLOT_MARKER}"
    return _compact_text(f"{text}{suffix}".strip(), 500)


async def _load_pacing_tail(conn: Any, *, now: datetime) -> tuple[datetime, int]:
    now_utc = _as_utc(now)
    cursor = await conn.execute(
        f"""
        SELECT next_attempt_at
        FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
        WHERE status = 'page_retry'
          AND next_attempt_at > ?
          AND instr(lower(last_error), ?) > 0
        ORDER BY next_attempt_at DESC
        LIMIT 1
        """,
        (now_utc.isoformat(), PAGE_PACING_SLOT_MARKER),
    )
    row = await cursor.fetchone()
    parsed = _parse_datetime(_row_value(row, "next_attempt_at", 0)) if row else None
    return max(now_utc, parsed or now_utc), 1


def _next_slot(
    tail: datetime,
    *,
    not_before: datetime,
) -> datetime:
    interval = timedelta(seconds=PAGE_HTML_PROBE_INTERVAL_SECONDS)
    return max(_as_utc(not_before), _as_utc(tail) + interval)


async def serialize_page_pacing_deferred_outcomes(
    conn: Any,
    outcomes: Iterable[Any],
) -> PagePacingScheduleResult:
    """Turn local pacing deferrals into a durable one-probe-per-minute schedule.

    Bulk persistence first records the canonical page_retry outcome. This helper
    then adjusts only rows from the just-finished batch whose retry kind is the
    local ``page_pacing_deferred`` condition. The existing 30-minute page retry
    floor remains the earliest retry time, while rows after that floor are spread
    into one-minute slots instead of waking as a herd.
    """

    deferred: list[tuple[str, datetime, str]] = []
    for outcome in outcomes:
        if getattr(outcome, "candidate", None) is not None:
            continue
        error = str(getattr(outcome, "error", "") or "")
        if not is_page_pacing_deferred(error):
            continue
        claim = getattr(outcome, "claim", None)
        item_id = str(getattr(claim, "item_id", "") or "").strip()
        observed = getattr(outcome, "observed_at", None)
        if not item_id or not isinstance(observed, datetime):
            continue
        deferred.append((item_id, _as_utc(observed), error))

    if not deferred:
        return PagePacingScheduleResult()

    deferred.sort(key=lambda value: (value[1], value[0]))
    anchor = max(value[1] for value in deferred)
    tail, statements = await _load_pacing_tail(conn, now=anchor)

    updates: list[tuple[str, str, str, str]] = []
    first_slot: datetime | None = None
    last_slot: datetime | None = None
    for item_id, observed_at, error in deferred:
        not_before = observed_at + timedelta(seconds=PAGE_RETRY_BASE_SECONDS)
        slot = _next_slot(tail, not_before=not_before)
        tail = slot
        first_slot = first_slot or slot
        last_slot = slot
        updates.append(
            (
                item_id,
                observed_at.isoformat(),
                slot.isoformat(),
                _mark_scheduled_error(error),
            )
        )

    placeholders = ",".join("(?, ?, ?, ?)" for _ in updates)
    params = tuple(value for row in updates for value in row)
    await conn.execute(
        f"""
        WITH pacing_updates (
            item_id, expected_last_attempt_at, next_attempt_at, last_error
        ) AS (
            VALUES {placeholders}
        )
        UPDATE {QUEUE_TABLE} AS target
        SET next_attempt_at = pacing_updates.next_attempt_at,
            last_error = pacing_updates.last_error
        FROM pacing_updates
        WHERE target.item_id = pacing_updates.item_id
          AND target.status = 'page_retry'
          AND target.last_attempt_at = pacing_updates.expected_last_attempt_at
          AND instr(lower(target.last_error), ?) > 0
        """,
        (*params, PAGE_PACING_DEFERRED_TOKEN),
    )
    statements += 1
    return PagePacingScheduleResult(
        rows=len(updates),
        sql_statements=statements,
        first_slot=first_slot,
        last_slot=last_slot,
    )


async def normalize_legacy_page_pacing_debt(
    conn: Any,
    *,
    now: datetime,
    limit: int = PAGE_PACING_DEBT_BATCH_LIMIT,
) -> int:
    """Serialize pre-v290 pacing rows without touching real page failures.

    PR #288 production telemetry proved the saturated page lane was entirely
    ``page_pacing_deferred``. Those rows were written before durable slotting and
    can therefore all mature at once. Migrate at most ``limit`` retained,
    unleased legacy rows per worker cycle. Existing not-before timestamps are
    never shortened, page-request cadence remains one per minute, and unrelated
    page failures are untouched.
    """

    if bool(getattr(conn, _LEGACY_COMPLETE_ATTR, False)):
        return 0

    now_utc = _as_utc(now)
    cutoff = (now_utc - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    bounded_limit = max(1, int(limit))
    cursor = await conn.execute(
        f"""
        SELECT item_id, next_attempt_at, last_error
        FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
        WHERE status = 'page_retry'
          AND last_seen_at >= ?
          AND (lease_until IS NULL OR lease_until < ?)
          AND instr(lower(last_error), ?) > 0
          AND instr(lower(last_error), ?) = 0
        ORDER BY next_attempt_at, priority_score DESC, last_seen_at DESC
        LIMIT ?
        """,
        (
            cutoff,
            now_utc.isoformat(),
            PAGE_PACING_DEFERRED_TOKEN,
            PAGE_PACING_SLOT_MARKER,
            bounded_limit,
        ),
    )
    rows = list(await cursor.fetchall())

    changed = 0
    first_slot: datetime | None = None
    last_slot: datetime | None = None
    if rows:
        tail, _ = await _load_pacing_tail(conn, now=now_utc)
        updates: list[tuple[str, str, str]] = []
        for row in rows:
            item_id = str(_row_value(row, "item_id", 0) or "").strip()
            if not item_id:
                continue
            not_before = _parse_datetime(_row_value(row, "next_attempt_at", 1)) or now_utc
            slot = _next_slot(tail, not_before=max(now_utc, not_before))
            tail = slot
            first_slot = first_slot or slot
            last_slot = slot
            updates.append(
                (
                    item_id,
                    slot.isoformat(),
                    _mark_scheduled_error(str(_row_value(row, "last_error", 2) or "")),
                )
            )

        if updates:
            placeholders = ",".join("(?, ?, ?)" for _ in updates)
            params = tuple(value for row in updates for value in row)
            await conn.execute(
                f"""
                WITH pacing_updates (item_id, next_attempt_at, last_error) AS (
                    VALUES {placeholders}
                )
                UPDATE {QUEUE_TABLE} AS target
                SET next_attempt_at = pacing_updates.next_attempt_at,
                    last_error = pacing_updates.last_error
                FROM pacing_updates
                WHERE target.item_id = pacing_updates.item_id
                  AND target.status = 'page_retry'
                  AND (target.lease_until IS NULL OR target.lease_until < ?)
                  AND instr(lower(target.last_error), ?) > 0
                  AND instr(lower(target.last_error), ?) = 0
                """,
                (
                    *params,
                    now_utc.isoformat(),
                    PAGE_PACING_DEFERRED_TOKEN,
                    PAGE_PACING_SLOT_MARKER,
                ),
            )
            changed = len(updates)
            await conn.commit()
            log.info(
                "Walmart page pacing debt serialized marker=%s legacy_rows=%s limit=%s "
                "slot_seconds=%s first_slot=%s last_slot=%s",
                PAGE_PACING_DEBT_MARKER,
                changed,
                bounded_limit,
                int(PAGE_HTML_PROBE_INTERVAL_SECONDS),
                first_slot.isoformat() if first_slot else "none",
                last_slot.isoformat() if last_slot else "none",
            )

    remaining_cursor = await conn.execute(
        f"""
        SELECT 1
        FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
        WHERE status = 'page_retry'
          AND last_seen_at >= ?
          AND instr(lower(last_error), ?) > 0
          AND instr(lower(last_error), ?) = 0
        LIMIT 1
        """,
        (cutoff, PAGE_PACING_DEFERRED_TOKEN, PAGE_PACING_SLOT_MARKER),
    )
    remaining = await remaining_cursor.fetchone()
    if remaining is None:
        try:
            setattr(conn, _LEGACY_COMPLETE_ATTR, True)
        except Exception:
            pass

    return changed


__all__ = [
    "PAGE_PACING_DEBT_BATCH_LIMIT",
    "PAGE_PACING_DEBT_MARKER",
    "PAGE_PACING_DEFERRED_TOKEN",
    "PAGE_PACING_SLOT_MARKER",
    "PagePacingScheduleResult",
    "is_page_pacing_deferred",
    "normalize_legacy_page_pacing_debt",
    "serialize_page_pacing_deferred_outcomes",
]
