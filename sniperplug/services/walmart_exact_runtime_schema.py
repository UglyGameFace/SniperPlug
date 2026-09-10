from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sniperplug.services.walmart_confirmed_no_price_legacy_cooldown import (
    LEGACY_COOLDOWN_BATCH_LIMIT,
    normalize_legacy_confirmed_no_price_cooldowns,
)
from sniperplug.services.walmart_exact_queue_runtime import TerminalIdentityMaintenance
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
)
from sniperplug.services.walmart_terminal_identity_maintenance import (
    maintain_terminal_identity_rows_limited as maintain_terminal_identity_rows,
)


log = logging.getLogger("sniperplug.autoscan")

TERMINAL_MAINTENANCE_INTERVAL_SECONDS = 15 * 60
LEGACY_CLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order"
LEGACY_CLAIM_ORDER_INDEX_V2 = f"idx_{QUEUE_TABLE}_claim_order_v2"
CLAIM_ORDER_INDEX = f"idx_{QUEUE_TABLE}_claim_order_v3"
FRESH_PROOF_DUE_INDEX = f"idx_{QUEUE_TABLE}_fresh_proof_due"
STATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"
STATUS_VERIFIED_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_verified_due"
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
    """Initialize exact runtime schema, indexes, and bounded compatibility state once.

    The production exact worker runs frequently. Reissuing all CREATE TABLE,
    CREATE INDEX, and COMMIT operations on every cycle creates avoidable Turso
    traffic and competes with queue claims and catalog writes. A newly connected
    database still performs the complete idempotent initialization.

    The v3 claim-order expression index matches the historical non-snapshot
    status ordering and remains available for compatibility. Production claim
    reads now use lane-specific bounded access paths: the fresh-proof and
    status/due indexes serve fresh/retry/page work, while the verified-age index
    lets scheduled recheck reads seek directly to rows old enough for the shared
    24h/72h/7d floors instead of scanning legacy early schedules. The one-time
    index build is intentionally small compared with repeatedly scanning the
    mature queue every 20 seconds.

    The same once-per-connection gate also normalizes one bounded slice of
    pre-#277 confirmed dual-no-price rows whose persisted retry timestamps still
    predate the six-hour cooldown policy. Only unambiguously stale rows are
    touched; post-attempt rediscovery and active leases remain untouched.
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
                    WHEN 'page_retry' THEN 3
                    WHEN 'verified_markdown' THEN 4
                    WHEN 'verified_under_threshold' THEN 5
                    WHEN 'verified_no_reference' THEN 6
                    WHEN 'verified_reference' THEN 7
                    WHEN 'not_buyable' THEN 8
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
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {STATUS_VERIFIED_DUE_INDEX}
            ON {QUEUE_TABLE} (
                status,
                verified_at,
                next_attempt_at,
                last_seen_at,
                lease_until,
                priority_score DESC
            )
            """
        )
        legacy_no_price_cooled = await normalize_legacy_confirmed_no_price_cooldowns(
            db,
            limit=LEGACY_COOLDOWN_BATCH_LIMIT,
        )
        await conn.commit()
        log.info(
            "Walmart confirmed no-price legacy cooldown normalization "
            "marker=legacy_no_price_floor_v278 cooled=%s limit=%s",
            legacy_no_price_cooled,
            LEGACY_COOLDOWN_BATCH_LIMIT,
        )
        await ensure_global_offer_memory_table(db)
        state.schema_ready = True


async def maintain_terminal_identity_rows_bounded(
    db: Any,
    *,
    now: datetime | None = None,
    interval_seconds: float = TERMINAL_MAINTENANCE_INTERVAL_SECONDS,
) -> TerminalIdentityMaintenance:
    """Run terminal cleanup at a bounded cadence and bounded row volume.

    Terminal statuses are excluded from ordinary claims immediately. The
    maintenance pass only pushes a limited slice of stale terminal timestamps
    forward and releases a small limited slice for weekly rediscovery reprobes.
    Bounding both cadence and row count prevents a maintenance tick from
    flooding the fresh lane or monopolizing the single isolated Turso worker.
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
