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


# Cleanup shares the single process-isolated Turso worker with live verification,
# Discord persistence, and other services. Production observed the previous
# 500-ID DELETE holding that worker for 8+ seconds. Keep each maintenance write
# much smaller; the production runner invokes this cleaner only after deal fanout
# and defers it while another catalog/reconciliation fanout owner is active.
CLEANUP_BATCH_SIZE = 100
CLEANUP_FOLLOWUP_SECONDS = 60
# Probe total queue size at most twice an hour. During overflow, amortize the
# discovered excess over small 100-ID post-delivery cleanup batches.
CLEANUP_OVERFLOW_PROBE_SECONDS = 30 * 60
CLEANUP_OVERFLOW_PROBE_BUDGET = 3000
# No overflow pruning of recently checked products or still-live alerts.
CLEANUP_OVERFLOW_PROTECT_MINUTES = 90
_STATE_ATTR = "_sniperplug_walmart_cleanup_state"
_FALLBACK_STATES: dict[int, "_CleanupState"] = {}


@dataclass
class _CleanupState:
    connection: Any
    next_due_monotonic: float = 0.0
    lock: asyncio.Lock | None = None
    next_overflow_probe_monotonic: float = 0.0
    overflow_remaining: int = 0


@dataclass(frozen=True)
class QueueCleanupResult:
    deleted: int = 0
    skipped_noop_write: bool = False
    expired_selected: int = 0
    overflow_selected: int = 0


async def maybe_prune_walmart_exact_queue_bounded(
    conn: Any,
    *,
    now: datetime | None = None,
) -> QueueCleanupResult:
    """Delete only preselected rows and never issue a blind no-op DELETE.

    A remote ``DELETE ... WHERE last_seen_at < ?`` still creates a write request
    even when zero rows qualify. Production showed that no-op statement taking
    almost fourteen seconds during startup, and later a 500-ID cleanup DELETE
    held the shared Turso worker for more than eight seconds. The local bounded
    preselection remains authoritative; the primary receives a small write only
    when exact item IDs actually need removal.
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

        now_iso = now_dt.astimezone(timezone.utc).isoformat()
        # First reclaim expired queue rows using the existing last_seen index.
        # Do not delete an in-flight active exact verification.
        active_guard = (
            "(status <> 'verifying' OR lease_until IS NULL OR lease_until <= ?)"
        )
        stale_cursor = await conn.execute(
            f"""
            SELECT item_id
            FROM {QUEUE_TABLE}
            WHERE last_seen_at < ?
              AND {active_guard}
            ORDER BY last_seen_at ASC
            LIMIT ?
            """,
            (cutoff, now_iso, CLEANUP_BATCH_SIZE),
        )
        expired_ids = _item_ids(await stale_cursor.fetchall())
        selected = list(expired_ids)
        overflow_ids: list[str] = []

        if len(selected) < CLEANUP_BATCH_SIZE:
            if (
                state.overflow_remaining <= 0
                and current >= state.next_overflow_probe_monotonic
            ):
                # COUNT(*) without a predicate uses SQLite's count fast path;
                # avoid the prior full-table CASE sort + OFFSET 50,000 on each
                # call, which exhausted Turso row-read budgets.
                count_cursor = await conn.execute(
                    f"SELECT COUNT(*) AS row_count FROM {QUEUE_TABLE}"
                )
                count_row = await count_cursor.fetchone()
                total_rows = _row_number(count_row, "row_count")
                state.overflow_remaining = min(
                    CLEANUP_OVERFLOW_PROBE_BUDGET,
                    max(0, total_rows - QUEUE_MAX_ROWS),
                )
                state.next_overflow_probe_monotonic = (
                    time.monotonic() + CLEANUP_OVERFLOW_PROBE_SECONDS
                )

            # Expired selections already count against the current excess:
            # never delete more healthy rows than needed to return below cap.
            state.overflow_remaining = max(
                0, state.overflow_remaining - len(expired_ids),
            )
            if state.overflow_remaining:
                # Don't evict fresh candidates or proof currently needed for
                # Discord delivery. The oldest rows are cheap to seek using the
                # existing last_seen index and are the least useful to retain.
                old_enough = (
                    now_dt.astimezone(timezone.utc)
                    - timedelta(minutes=CLEANUP_OVERFLOW_PROTECT_MINUTES)
                ).isoformat()
                room = min(
                    CLEANUP_BATCH_SIZE - len(selected),
                    state.overflow_remaining,
                )
                exclusions = ",".join("?" for _ in selected)
                not_selected = (
                    f"AND item_id NOT IN ({exclusions})" if selected else ""
                )
                overflow_cursor = await conn.execute(
                    f"""
                    SELECT item_id
                    FROM {QUEUE_TABLE}
                    WHERE last_seen_at < ?
                      AND (verified_at IS NULL OR verified_at < ?)
                      AND {active_guard}
                      {not_selected}
                    ORDER BY last_seen_at ASC
                    LIMIT ?
                    """,
                    (
                        old_enough, old_enough, now_iso,
                        *selected, room,
                    ),
                )
                for item_id in _item_ids(await overflow_cursor.fetchall()):
                    if item_id in selected or item_id in overflow_ids:
                        continue
                    overflow_ids.append(item_id)
                    if len(overflow_ids) >= room:
                        break
                selected.extend(overflow_ids)

        if not selected:
            state.overflow_remaining = 0
            state.next_due_monotonic = time.monotonic() + min(
                QUEUE_CLEANUP_INTERVAL_SECONDS,
                max(1.0, state.next_overflow_probe_monotonic - time.monotonic()),
            )
            return QueueCleanupResult(skipped_noop_write=True)

        # Guard against a rediscovery or fresh successful verification racing
        # between preselection and deletion. At most one 100-ID write follows
        # the existing post-fanout owner, never a bulk deletion in intake.
        old_enough = (
            now_dt.astimezone(timezone.utc)
            - timedelta(minutes=CLEANUP_OVERFLOW_PROTECT_MINUTES)
        ).isoformat()
        placeholders = ", ".join("?" for _ in selected)
        await conn.execute(
            f"""
            DELETE FROM {QUEUE_TABLE}
            WHERE item_id IN ({placeholders})
              AND last_seen_at < ?
              AND (verified_at IS NULL OR verified_at < ?)
              AND {active_guard}
            """,
            (*selected, old_enough, old_enough, now_iso),
        )
        await conn.commit()

        # Any expired row reclaimed also reduces the true overflow; avoid
        # repeatedly evicting healthy rows after the budget is exhausted.
        state.overflow_remaining = max(
            0, state.overflow_remaining - len(overflow_ids),
        )
        state.next_due_monotonic = time.monotonic() + (
            CLEANUP_FOLLOWUP_SECONDS
            if len(selected) >= CLEANUP_BATCH_SIZE or state.overflow_remaining
            else min(
                QUEUE_CLEANUP_INTERVAL_SECONDS,
                max(1.0, state.next_overflow_probe_monotonic - time.monotonic()),
            )
        )
        return QueueCleanupResult(
            deleted=len(selected),
            expired_selected=len(expired_ids),
            overflow_selected=len(overflow_ids),
        )

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


def _row_number(row: Any, key: str) -> int:
    if row is None:
        return 0
    try:
        return max(0, int(row[key]))
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    try:
        return max(0, int(row[0]))
    except (TypeError, ValueError, KeyError, IndexError):
        return 0
