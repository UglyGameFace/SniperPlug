from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_CLEANUP_INTERVAL_SECONDS,
    QUEUE_MAX_ROWS,
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
)


CLEANUP_BATCH_SIZE = 500
CLEANUP_FOLLOWUP_SECONDS = 5 * 60
_STATE_ATTR = "_sniperplug_walmart_cleanup_state"
_FALLBACK_STATES: dict[int, "_CleanupState"] = {}


@dataclass
class _CleanupState:
    connection: Any
    next_due_monotonic: float = 0.0
    lock: asyncio.Lock | None = None


@dataclass(frozen=True)
class QueueCleanupResult:
    deleted: int = 0
    skipped_noop_write: bool = False


async def maybe_prune_walmart_exact_queue_bounded(
    conn: Any,
    *,
    now: datetime | None = None,
) -> QueueCleanupResult:
    """Delete only preselected rows and never issue a blind no-op DELETE.

    A remote ``DELETE ... WHERE last_seen_at < ?`` still creates a write request
    even when zero rows qualify. Production showed that no-op statement taking
    almost fourteen seconds during startup. The local replica now identifies a
    bounded set first; the Turso primary receives a write only when exact item IDs
    actually need removal.
    """

    state = _state_for(conn)
    current = time.monotonic()
    if current < state.next_due_monotonic:
        return QueueCleanupResult()
    if state.lock is None:
        state.lock = asyncio.Lock()

    async with state.lock:
        current = time.monotonic()
        if current < state.next_due_monotonic:
            return QueueCleanupResult()

        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        cutoff = (
            now_dt.astimezone(timezone.utc)
            - timedelta(days=QUEUE_RETENTION_DAYS)
        ).isoformat()

        stale_cursor = await conn.execute(
            f"""
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE last_seen_at < ?
            ORDER BY last_seen_at ASC
            LIMIT ?
            """,
            (cutoff, CLEANUP_BATCH_SIZE),
        )
        stale_rows = await stale_cursor.fetchall()
        item_ids = _item_ids(stale_rows)

        remaining = max(0, CLEANUP_BATCH_SIZE - len(item_ids))
        if remaining:
            overflow_cursor = await conn.execute(
                f"""
                SELECT item_id
                FROM {QUEUE_TABLE}
                ORDER BY
                    CASE WHEN status = 'verified_markdown' THEN 0 ELSE 1 END,
                    priority_score DESC,
                    last_seen_at DESC
                LIMIT ? OFFSET ?
                """,
                (remaining, QUEUE_MAX_ROWS),
            )
            for item_id in _item_ids(await overflow_cursor.fetchall()):
                if item_id not in item_ids:
                    item_ids.append(item_id)

        if not item_ids:
            state.next_due_monotonic = (
                time.monotonic() + QUEUE_CLEANUP_INTERVAL_SECONDS
            )
            return QueueCleanupResult(skipped_noop_write=True)

        placeholders = ", ".join("?" for _ in item_ids)
        await conn.execute(
            f"DELETE FROM {QUEUE_TABLE} WHERE item_id IN ({placeholders})",
            tuple(item_ids),
        )
        await conn.commit()
        state.next_due_monotonic = time.monotonic() + (
            CLEANUP_FOLLOWUP_SECONDS
            if len(item_ids) >= CLEANUP_BATCH_SIZE
            else QUEUE_CLEANUP_INTERVAL_SECONDS
        )
        return QueueCleanupResult(deleted=len(item_ids))


def _state_for(conn: Any) -> _CleanupState:
    state = getattr(conn, _STATE_ATTR, None)
    if isinstance(state, _CleanupState):
        return state
    state = _FALLBACK_STATES.get(id(conn))
    if state is None or state.connection is not conn:
        state = _CleanupState(connection=conn)
        try:
            setattr(conn, _STATE_ATTR, state)
        except Exception:
            _FALLBACK_STATES[id(conn)] = state
    return state


def _item_ids(rows: Any) -> list[str]:
    item_ids: list[str] = []
    for row in list(rows or []):
        value: Any = None
        try:
            value = row["item_id"]
        except Exception:
            try:
                value = row[0]
            except Exception:
                value = None
        item_id = str(value or "").strip()
        if item_id and item_id not in item_ids:
            item_ids.append(item_id)
    return item_ids
