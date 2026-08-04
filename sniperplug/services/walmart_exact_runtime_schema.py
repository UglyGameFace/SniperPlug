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
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
)


TERMINAL_MAINTENANCE_INTERVAL_SECONDS = 15 * 60
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
    """Initialize exact-queue and offer-memory schema once per connection.

    The production exact worker runs every minute. Reissuing all CREATE TABLE,
    CREATE INDEX, and COMMIT operations on every cycle creates avoidable remote
    Turso traffic and competes with queue claims and catalog writes. A newly
    connected database still performs the complete idempotent initialization.
    """

    conn = db.require_conn()
    state = _state_for(conn)
    if state.schema_ready:
        return

    async with _lock(state, "schema_lock"):
        if state.schema_ready:
            return
        await ensure_walmart_exact_verification_queue(db)
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
