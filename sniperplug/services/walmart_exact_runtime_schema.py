from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sniperplug.services.walmart_exact_queue_runtime import (
    TerminalIdentityMaintenance,
    maintain_terminal_identity_rows,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
)


TERMINAL_MAINTENANCE_INTERVAL_SECONDS = 15 * 60
LEGACY_CLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order"
LEGACY_CLAIM_ORDER_INDEX_V2 = f"idx_{QUEUE_TABLE}_claim_order_v2"
CLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order_v3"
FRESH_PROOF_DUE_INDEX = f"idx_{QUEUE_TABLE}_fresh_proof_due"
STATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"
_STATE_ATTR = "_sniperplug_walmart_exact_runtime_state"
_FALLBACK_STATES: dict[int, "_RuntimeState"] = {}


@dataclass
class _RuntimeState:
    connection: Any
    schema_ready: bool = False
    schema_lock: asyncio.Lock | None = None
    maintenance_lock: asyncio.Lock | None = None
    last_maintenance_monotonic: float | None = None


def _state_for(conn: Any) -> _RuntimeState:
    state = getattr(conn, _STATE_ATTR, None)
    if isinstance(state, _RuntimeState):
        return state

    state = _FALLBACK_STATES.get(id(conn))
    if state is None or state.connection is not conn:
        state = _RuntimeState(connection=conn)
        try:
            setattr(conn, _STATE_ATTR, state)
        except Exception:
            _FALLBACK_STATES[id(conn)] = state
    return state


def _lock(state: _RuntimeState, name: str) -> asyncio.Lock:
    existing = getattr(state, name)
    if existing is None:
        existing = asyncio.Lock()
        setattr(state, name, existing)
    return existing


async def ensure_exact_runtime_schema_once(db: Any) -> None:
    """Initialize exact runtime schema and its claim/pressure indexes once.

    The production exact worker runs frequently. Reissuing all CREATE TABLE,
    CREATE INDEX, and COMMIT operations on every cycle creates avoidable Turso
    traffic and competes with queue claims and catalog writes. A newly connected
    database still performs the complete idempotent initialization.

    The v3 claim-order expression index matches the non-snapshot status ordering
    used after the new fresh-proof lane. A separate partial fresh-proof index
    keeps recent discovery snapshots addressable without forcing the page-retry
    backlog through the normal claim order. The status/due index serves bounded
    scheduler pressure reads and the page-recovery subquery.
    """

    conn = db.require_conn()
    state = _state_for(conn)
    if state.schema_ready:
        return

    async with _lock(state, "schema_lock"):
        if state.schema_ready:
            return
        await ensure_walmart_exact_verification_queue(db)
        await conn.execute(f"DROP INDEX IF EXISTS {LEGACY_CLAIM_ORDER_INDEX}")
        await conn.execute(f"DROP INDEX IF EXISTS {LEGACY_CLAIM_ORDER_INDEX_V2}")
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {CLAIM_ORDER_INDEX}
            ON {QUEUE_TABLE} (
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'retry' THEN 1
                    WHEN 'failed' THEN 2
                    WHEN 'verified_markdown' THEN 3
                    WHEN 'verified_under_threshold' THEN 4
                    WHEN 'verified_no_reference' THEN 5
                    WHEN 'verified_reference' THEN 6
                    WHEN 'not_buyable' THEN 7
                    WHEN 'page_retry' THEN 8
                    WHEN 'verifying' THEN 9
                    ELSE 10
                END,
                priority_score DESC,
                last_seen_at DESC,
                next_attempt_at,
                lease_until
            )
            WHERE status NOT IN ('incomplete_identity', 'identity_mismatch')
            """
        )
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {FRESH_PROOF_DUE_INDEX}
            ON {QUEUE_TABLE} (
                discovery_snapshot_at DESC,
                next_attempt_at,
                last_seen_at,
                lease_until,
                priority_score DESC
            )
            WHERE discovery_snapshot_json <> ''
              AND status NOT IN ('incomplete_identity', 'identity_mismatch')
            """
        )
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {STATUS_DUE_INDEX}
            ON {QUEUE_TABLE} (
                status,
                next_attempt_at,
                last_seen_at,
                lease_until,
                priority_score DESC
            )
            """
        )
        await conn.commit()
        await ensure_global_offer_memory_table(db)
        state.schema_ready = True


async def maintain_terminal_identity_rows_bounded(
    db: Any,
    *,
    now: datetime | None = None,
    interval_seconds: float = TERMINAL_MAINTENANCE_INTERVAL_SECONDS,
) -> TerminalIdentityMaintenance:
    """Run terminal identity cleanup at a bounded cadence, not every minute.

    Terminal statuses are already excluded from ordinary claims immediately.
    The maintenance pass only pushes their next-attempt timestamp far forward
    and performs the low-frequency weekly rediscovery reprobe, so running it
    every fifteen minutes preserves fail-closed behavior without two remote
    UPDATE scans on every queue cycle.
    """

    await ensure_exact_runtime_schema_once(db)
    conn = db.require_conn()
    state = _state_for(conn)
    current = time.monotonic()
    interval = max(60.0, float(interval_seconds))
    last = state.last_maintenance_monotonic
    if last is not None and current - last < interval:
        return TerminalIdentityMaintenance()

    async with _lock(state, "maintenance_lock"):
        current = time.monotonic()
        last = state.last_maintenance_monotonic
        if last is not None and current - last < interval:
            return TerminalIdentityMaintenance()
        result = await maintain_terminal_identity_rows(db, now=now)
        state.last_maintenance_monotonic = time.monotonic()
        return result
