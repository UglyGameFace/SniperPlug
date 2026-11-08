from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


log = logging.getLogger("sniperplug.autoscan")

TABLE = "walmart_worker_cycle_health"
WORKERS = ("exact", "catalog")
_STATE_ATTR = "_sniperplug_walmart_worker_cycle_health_ready"
_LAST_ERROR_LIMIT = 500


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


# Consecutive failed cycles before /autoscan_health flags the worker as
# needing attention. Env-overridable; 3 keeps a single transient provider
# blip quiet while a stuck worker surfaces within about a minute.
FAILURE_ALERT_THRESHOLD = _env_int(
    "WALMART_WORKER_CYCLE_FAILURE_ALERT_THRESHOLD", 3
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkerCycleHealthRow:
    worker: str = ""
    consecutive_failures: int = 0
    total_cycles: int = 0
    total_failures: int = 0
    total_timeouts: int = 0
    last_cycle_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    last_api_price_missing: int = 0
    last_batch_claimed: int = 0
    last_batch_at: str | None = None

    @property
    def last_batch_missing_rate(self) -> float | None:
        claimed = int(self.last_batch_claimed or 0)
        if claimed <= 0:
            return None
        return float(self.last_api_price_missing or 0) / float(claimed)

    def summary_fragment(self) -> str:
        label = "exact worker" if self.worker == "exact" else "catalog worker"
        if self.total_cycles == 0:
            return f"{label}: no cycles recorded yet"
        state = (
            "healthy"
            if int(self.consecutive_failures or 0) == 0
            else f"**{self.consecutive_failures} consecutive failures**"
        )
        detail = (
            f"{label}: {state} • total failures "
            f"**{self.total_failures}** (timeouts **{self.total_timeouts}**) • "
            f"last success `{self.last_success_at or 'never'}`"
        )
        if self.last_failure_at:
            detail += f" • last failure `{self.last_failure_at}`"
            if self.last_error:
                detail += f" `{_trim_text(self.last_error, 120)}`"
        if self.last_batch_at:
            rate = self.last_batch_missing_rate
            rate_text = (
                f" ({rate * 100:.0f}% of batch)"
                if rate is not None
                else ""
            )
            detail += (
                f" • last batch **{self.last_batch_claimed}** claimed, "
                f"**{self.last_api_price_missing}** api_price_missing{rate_text}"
            )
        return detail


@dataclass(frozen=True)
class WalmartWorkerCycleHealth:
    exact: WorkerCycleHealthRow = WorkerCycleHealthRow(worker="exact")
    catalog: WorkerCycleHealthRow = WorkerCycleHealthRow(worker="catalog")
    available: bool = False

    def needs_attention(self, *, threshold: int = FAILURE_ALERT_THRESHOLD) -> bool:
        limit = max(1, int(threshold))
        return (
            int(self.exact.consecutive_failures or 0) >= limit
            or int(self.catalog.consecutive_failures or 0) >= limit
        )

    def summary_line(self) -> str:
        if not self.available:
            return (
                "Worker cycle health: no durable state yet — counters start "
                "on the next exact/catalog cycle."
            )
        return (
            self.exact.summary_fragment()
            + "\n"
            + self.catalog.summary_fragment()
        )


def _trim_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


async def ensure_worker_cycle_health_schema(db: Any) -> None:
    """Create the cycle-health table once per live database connection."""

    if db is None:
        return
    conn = db.require_conn()
    if getattr(conn, _STATE_ATTR, False):
        return
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            worker TEXT PRIMARY KEY,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            total_cycles INTEGER NOT NULL DEFAULT 0,
            total_failures INTEGER NOT NULL DEFAULT 0,
            total_timeouts INTEGER NOT NULL DEFAULT 0,
            last_cycle_at TEXT,
            last_success_at TEXT,
            last_failure_at TEXT,
            last_error TEXT,
            last_api_price_missing INTEGER NOT NULL DEFAULT 0,
            last_batch_claimed INTEGER NOT NULL DEFAULT 0,
            last_batch_at TEXT
        )
        """
    )
    await conn.commit()
    try:
        setattr(conn, _STATE_ATTR, True)
    except Exception:
        pass


async def record_worker_cycle_outcome(
    db: Any,
    worker: str,
    *,
    ok: bool,
    error: str | None = None,
    timed_out: bool = False,
    api_price_missing: int | None = None,
    claimed: int | None = None,
) -> None:
    """Record one worker-cycle outcome. Best-effort: never raises.

    A quota pause is not an outcome — the worker deliberately did not run —
    so callers must not record it here; the counters only move on cycles
    that actually attempted provider work. A success resets the consecutive
    failure streak; a failure increments it and keeps the last error text.
    """

    if db is None or str(worker) not in WORKERS:
        return
    try:
        await ensure_worker_cycle_health_schema(db)
        conn = db.require_conn()
        now = _utcnow_iso()
        if ok:
            await conn.execute(
                f"""
                INSERT INTO {TABLE} (
                    worker, consecutive_failures, total_cycles, total_failures,
                    total_timeouts, last_cycle_at, last_success_at,
                    last_api_price_missing, last_batch_claimed, last_batch_at
                ) VALUES (?, 0, 1, 0, 0, ?, ?, ?, ?, ?)
                ON CONFLICT(worker) DO UPDATE SET
                    consecutive_failures = 0,
                    total_cycles = total_cycles + 1,
                    last_cycle_at = excluded.last_cycle_at,
                    last_success_at = excluded.last_success_at,
                    last_api_price_missing = excluded.last_api_price_missing,
                    last_batch_claimed = excluded.last_batch_claimed,
                    last_batch_at = excluded.last_batch_at
                """,
                (
                    str(worker),
                    now,
                    now,
                    int(api_price_missing or 0),
                    int(claimed or 0),
                    now,
                ),
            )
        else:
            await conn.execute(
                f"""
                INSERT INTO {TABLE} (
                    worker, consecutive_failures, total_cycles, total_failures,
                    total_timeouts, last_cycle_at, last_failure_at, last_error
                ) VALUES (?, 1, 1, 1, ?, ?, ?, ?)
                ON CONFLICT(worker) DO UPDATE SET
                    consecutive_failures = consecutive_failures + 1,
                    total_cycles = total_cycles + 1,
                    total_failures = total_failures + 1,
                    total_timeouts = total_timeouts + excluded.total_timeouts,
                    last_cycle_at = excluded.last_cycle_at,
                    last_failure_at = excluded.last_failure_at,
                    last_error = excluded.last_error
                """,
                (
                    str(worker),
                    1 if timed_out else 0,
                    now,
                    now,
                    _trim_text(error or "unknown error", _LAST_ERROR_LIMIT),
                ),
            )
        await conn.commit()
    except Exception:
        # Cycle-health bookkeeping must never turn a worker cycle into a
        # failure, and must never mask the real outcome in the caller.
        log.debug("Walmart worker cycle-health record failed safely", exc_info=True)


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def load_worker_cycle_health(db: Any) -> WalmartWorkerCycleHealth:
    """Return durable per-worker cycle counters, fail-safe when unavailable."""

    empty = WalmartWorkerCycleHealth(available=False)
    if db is None:
        return empty
    try:
        await ensure_worker_cycle_health_schema(db)
        conn = db.require_conn()
        cursor = await conn.execute(
            f"""
            SELECT worker, consecutive_failures, total_cycles, total_failures,
                   total_timeouts, last_cycle_at, last_success_at,
                   last_failure_at, last_error, last_api_price_missing,
                   last_batch_claimed, last_batch_at
            FROM {TABLE}
            """
        )
        rows = await cursor.fetchall()
    except Exception:
        return empty

    by_worker: dict[str, WorkerCycleHealthRow] = {}
    for row in rows or ():
        worker = str(_row_get(row, "worker", 0) or "")
        if worker not in WORKERS:
            continue
        by_worker[worker] = WorkerCycleHealthRow(
            worker=worker,
            consecutive_failures=_as_int(_row_get(row, "consecutive_failures", 1)),
            total_cycles=_as_int(_row_get(row, "total_cycles", 2)),
            total_failures=_as_int(_row_get(row, "total_failures", 3)),
            total_timeouts=_as_int(_row_get(row, "total_timeouts", 4)),
            last_cycle_at=_row_get(row, "last_cycle_at", 5),
            last_success_at=_row_get(row, "last_success_at", 6),
            last_failure_at=_row_get(row, "last_failure_at", 7),
            last_error=_row_get(row, "last_error", 8),
            last_api_price_missing=_as_int(_row_get(row, "last_api_price_missing", 9)),
            last_batch_claimed=_as_int(_row_get(row, "last_batch_claimed", 10)),
            last_batch_at=_row_get(row, "last_batch_at", 11),
        )
    return WalmartWorkerCycleHealth(
        exact=by_worker.get("exact", WorkerCycleHealthRow(worker="exact")),
        catalog=by_worker.get("catalog", WorkerCycleHealthRow(worker="catalog")),
        available=True,
    )
