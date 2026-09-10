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
LEGACY_COOLDOWN_PASS_INTERVAL_SECONDS = 15.0
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
    legacy_cooldown_complete: bool = False
    legacy_cooldown_batches: int = 0
    legacy_cooldown_last_pass_monotonic: float | None = None


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


def _monotonic() -> float:
    """Return the runtime clock without exposing asyncio's shared time module to tests."""

    return time.monotonic()


def _legacy_cooldown_pass_due(
    state: _RuntimeState,
    *,
    now_monotonic: float,
) -> bool:
    if state.legacy_cooldown_complete:
        return False
    last = state.legacy_cooldown_last_pass_monotonic
    return (
        last is None
        or float(now_monotonic) - float(last) >= LEGACY_COOLDOWN_PASS_INTERVAL_SECONDS
    )


async def ensure_exact_runtime_schema_once(db: Any) -> None:
    """Initialize exact runtime schema and finish bounded legacy cooldown catch-up.

    The production exact worker runs frequently. Reissuing all CREATE TABLE,
    CREATE INDEX, and COMMIT operations on every cycle creates avoidable Turso
    traffic and competes with queue claims and catalog writes. A newly connected
    database still performs the complete idempotent schema initialization once.

    The v3 claim-order expression index matches the historical non-snapshot
    status ordering and remains available for compatibility. Production claim
    reads now use lane-specific bounded access paths: the fresh-proof and
    status/due indexes serve fresh/retry/page work, while the verified-age index
    lets scheduled recheck reads seek directly to rows old enough for the shared
    24h/72h/7d floors instead of scanning legacy early schedules. The one-time
    index build is intentionally small compared with repeatedly scanning the
    mature queue every 20 seconds.

    PR #278 added a conservative 200-row compatibility pass for pre-#277
    confirmed dual-no-price retry schedules. Production can contain more than one
    bounded slice, so stopping after the first pass leaves later legacy rows able
    to mature in waves. Keep every pass capped at the same 200 rows, but repeat
    one pass per exact-worker cycle until a pass changes zero rows. A short
    monotonic cadence guard prevents the worker's immediate nested schema check
    from running a second migration batch in the same cycle. The in-memory
    completion flag then removes the query from steady state for the rest of the
    live connection. Post-attempt rediscovery and active leases remain protected
    by the unchanged normalizer predicates.
    """

    conn = db.require_conn()
    state = _state_for(conn)
    current_monotonic = _monotonic()
    legacy_due = _legacy_cooldown_pass_due(
        state,
        now_monotonic=current_monotonic,
    )
    if state.schema_ready and not legacy_due:
        return

    async with _lock(state, "schema_lock"):
        current_monotonic = _monotonic()
        legacy_due = _legacy_cooldown_pass_due(
            state,
            now_monotonic=current_monotonic,
        )
        if state.schema_ready and not legacy_due:
            return

        initialized_now = not state.schema_ready
        if initialized_now:
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

        legacy_no_price_cooled = 0
        ran_legacy_catchup = legacy_due
        if ran_legacy_catchup:
            legacy_no_price_cooled = await normalize_legacy_confirmed_no_price_cooldowns(
                db,
                limit=LEGACY_COOLDOWN_BATCH_LIMIT,
            )
            state.legacy_cooldown_batches += 1
            state.legacy_cooldown_last_pass_monotonic = _monotonic()
            # A zero-change pass is the only durable indication that no matching
            # legacy row remains. Any positive count schedules one more bounded
            # pass on a later worker cycle, including a partially raced batch
            # that updated fewer than the 200-row selection ceiling.
            state.legacy_cooldown_complete = legacy_no_price_cooled == 0

        if initialized_now or legacy_no_price_cooled > 0:
            await conn.commit()

        if initialized_now:
            await ensure_global_offer_memory_table(db)
            state.schema_ready = True

        if ran_legacy_catchup:
            log.info(
                "Walmart confirmed no-price legacy cooldown normalization "
                "marker=legacy_no_price_floor_v278 "
                "catchup=legacy_no_price_multibatch_v279 cooled=%s limit=%s "
                "complete=%s batch=%s",
                legacy_no_price_cooled,
                LEGACY_COOLDOWN_BATCH_LIMIT,
                str(state.legacy_cooldown_complete).lower(),
                state.legacy_cooldown_batches,
            )


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
