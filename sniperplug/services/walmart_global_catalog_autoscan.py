from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_catalog_coverage import catalog_route_pool
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth


STATE_TABLE = "walmart_global_catalog_autoscan_state"
STATE_KEY = "walmart"
DEFAULT_ROUTES_PER_BATCH = 4
CLAIM_LEASE_SECONDS = 15 * 60
DEFAULT_ACTIONABLE_QUEUE_LIMIT = 450


@dataclass(frozen=True)
class GlobalCatalogClaim:
    token: str
    start_index: int
    queries: tuple[str, ...]
    total_routes: int
    completed_routes_before: int
    completed_passes_before: int

    @property
    def next_index(self) -> int:
        if self.total_routes <= 0:
            return 0
        return (self.start_index + len(self.queries)) % self.total_routes

    @property
    def wraps_catalog(self) -> bool:
        return bool(
            self.total_routes > 0
            and self.start_index + len(self.queries) >= self.total_routes
        )

    def summary_line(self) -> str:
        end = self.start_index + len(self.queries)
        return (
            "global catalog claim: "
            f"routes **{self.start_index + 1}-{min(end, self.total_routes)}**/"
            f"**{self.total_routes}** • pass **{self.completed_passes_before + 1}**"
        )


@dataclass(frozen=True)
class GlobalCatalogState:
    cursor_index: int = 0
    completed_routes: int = 0
    completed_passes: int = 0
    claim_active: bool = False
    last_started_at: str = ""
    last_completed_at: str = ""
    last_error: str = ""

    def summary_line(self, *, total_routes: int) -> str:
        return (
            "global catalog state: "
            f"next route **{self.cursor_index + 1 if total_routes else 0}/{total_routes}** • "
            f"completed routes **{self.completed_routes}** • full passes **{self.completed_passes}** • "
            f"claim active **{'yes' if self.claim_active else 'no'}**"
        )


async def ensure_global_catalog_state(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
            state_key TEXT PRIMARY KEY,
            cursor_index INTEGER NOT NULL DEFAULT 0,
            completed_routes INTEGER NOT NULL DEFAULT 0,
            completed_passes INTEGER NOT NULL DEFAULT 0,
            claim_token TEXT NOT NULL DEFAULT '',
            claim_start_index INTEGER,
            claim_route_count INTEGER NOT NULL DEFAULT 0,
            lease_until TEXT,
            last_started_at TEXT,
            last_completed_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    now = _utc_now().isoformat()
    await conn.execute(
        f"""
        INSERT INTO {STATE_TABLE} (
            state_key, cursor_index, completed_routes, completed_passes,
            claim_token, claim_route_count, last_error, updated_at
        )
        VALUES (?, 0, 0, 0, '', 0, '', ?)
        ON CONFLICT(state_key) DO NOTHING
        """,
        (STATE_KEY, now),
    )
    await conn.commit()


async def claim_next_catalog_routes(
    db: Any,
    *,
    route_count: int = DEFAULT_ROUTES_PER_BATCH,
    now: datetime | None = None,
) -> GlobalCatalogClaim | None:
    """Lease the next deterministic catalog slice without skipping on restart.

    The cursor advances only after ``complete_catalog_claim`` succeeds. A crash
    therefore repeats at most one small route batch rather than silently losing
    part of the catalog. The state is global, not guild-specific, so adding more
    Discord servers never multiplies Walmart API traffic.
    """

    await ensure_global_catalog_state(db)
    pool = catalog_route_pool()
    if not pool:
        return None

    conn = db.require_conn()
    now_dt = now or _utc_now()
    now_iso = now_dt.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT cursor_index, completed_routes, completed_passes,
               claim_token, lease_until
        FROM {STATE_TABLE}
        WHERE state_key = ?
        """,
        (STATE_KEY,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    active_token = str(_row_get(row, "claim_token", 3) or "").strip()
    lease_until = _parse_datetime(_row_get(row, "lease_until", 4))
    if active_token and lease_until is not None and lease_until > now_dt:
        return None

    total_routes = len(pool)
    count = max(1, min(int(route_count), total_routes))
    start_index = _as_int(_row_get(row, "cursor_index", 0)) % total_routes
    selected = list(pool[start_index : start_index + count])
    if len(selected) < count:
        selected.extend(pool[: count - len(selected)])

    token = uuid.uuid4().hex
    lease_iso = (now_dt + timedelta(seconds=CLAIM_LEASE_SECONDS)).isoformat()
    await conn.execute(
        f"""
        UPDATE {STATE_TABLE}
        SET claim_token = ?, claim_start_index = ?, claim_route_count = ?,
            lease_until = ?, last_started_at = ?, last_error = '', updated_at = ?
        WHERE state_key = ?
          AND (
              claim_token = '' OR lease_until IS NULL OR lease_until <= ?
          )
        """,
        (
            token,
            start_index,
            count,
            lease_iso,
            now_iso,
            now_iso,
            STATE_KEY,
            now_iso,
        ),
    )
    verify = await conn.execute(
        f"SELECT claim_token FROM {STATE_TABLE} WHERE state_key = ?",
        (STATE_KEY,),
    )
    verify_row = await verify.fetchone()
    await conn.commit()
    if str(_row_get(verify_row, "claim_token", 0) or "") != token:
        return None

    return GlobalCatalogClaim(
        token=token,
        start_index=start_index,
        queries=tuple(selected),
        total_routes=total_routes,
        completed_routes_before=_as_int(_row_get(row, "completed_routes", 1)),
        completed_passes_before=_as_int(_row_get(row, "completed_passes", 2)),
    )


async def complete_catalog_claim(
    db: Any,
    claim: GlobalCatalogClaim,
    *,
    now: datetime | None = None,
) -> bool:
    await ensure_global_catalog_state(db)
    conn = db.require_conn()
    now_iso = (now or _utc_now()).isoformat()
    next_index = claim.next_index
    completed_passes = claim.completed_passes_before + int(claim.wraps_catalog)
    completed_routes = claim.completed_routes_before + len(claim.queries)
    await conn.execute(
        f"""
        UPDATE {STATE_TABLE}
        SET cursor_index = ?, completed_routes = ?, completed_passes = ?,
            claim_token = '', claim_start_index = NULL, claim_route_count = 0,
            lease_until = NULL, last_completed_at = ?, last_error = '', updated_at = ?
        WHERE state_key = ? AND claim_token = ?
        """,
        (
            next_index,
            completed_routes,
            completed_passes,
            now_iso,
            now_iso,
            STATE_KEY,
            claim.token,
        ),
    )
    verify = await conn.execute(
        f"SELECT claim_token, cursor_index FROM {STATE_TABLE} WHERE state_key = ?",
        (STATE_KEY,),
    )
    row = await verify.fetchone()
    await conn.commit()
    return (
        str(_row_get(row, "claim_token", 0) or "") == ""
        and _as_int(_row_get(row, "cursor_index", 1)) == next_index
    )


async def release_catalog_claim(
    db: Any,
    claim: GlobalCatalogClaim,
    *,
    error: str,
    now: datetime | None = None,
) -> None:
    """Release a failed claim without advancing the durable cursor."""

    await ensure_global_catalog_state(db)
    conn = db.require_conn()
    now_iso = (now or _utc_now()).isoformat()
    await conn.execute(
        f"""
        UPDATE {STATE_TABLE}
        SET claim_token = '', claim_start_index = NULL, claim_route_count = 0,
            lease_until = NULL, last_error = ?, updated_at = ?
        WHERE state_key = ? AND claim_token = ?
        """,
        (_clean_error(error), now_iso, STATE_KEY, claim.token),
    )
    await conn.commit()


async def load_global_catalog_state(db: Any) -> GlobalCatalogState:
    await ensure_global_catalog_state(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT cursor_index, completed_routes, completed_passes, claim_token,
               lease_until, last_started_at, last_completed_at, last_error
        FROM {STATE_TABLE}
        WHERE state_key = ?
        """,
        (STATE_KEY,),
    )
    row = await cursor.fetchone()
    now = _utc_now()
    lease_until = _parse_datetime(_row_get(row, "lease_until", 4))
    active = bool(
        str(_row_get(row, "claim_token", 3) or "").strip()
        and lease_until is not None
        and lease_until > now
    )
    return GlobalCatalogState(
        cursor_index=_as_int(_row_get(row, "cursor_index", 0)),
        completed_routes=_as_int(_row_get(row, "completed_routes", 1)),
        completed_passes=_as_int(_row_get(row, "completed_passes", 2)),
        claim_active=active,
        last_started_at=str(_row_get(row, "last_started_at", 5) or ""),
        last_completed_at=str(_row_get(row, "last_completed_at", 6) or ""),
        last_error=str(_row_get(row, "last_error", 7) or ""),
    )


def catalog_backpressure_reason(
    health: WalmartExactQueueHealth,
    *,
    actionable_limit: int = DEFAULT_ACTIONABLE_QUEUE_LIMIT,
) -> str | None:
    """Pause discovery when exact verification cannot keep up.

    Fail-closed identity-unavailable rows are excluded from actionable backlog;
    they remain visible in health metrics but cannot be fixed by hammering the
    exact endpoint again. Pending, verifying, and non-identity due work controls
    discovery pressure.
    """

    non_identity_due = max(0, int(health.due_now) - int(health.identity_blocked))
    actionable = int(health.pending) + int(health.verifying) + non_identity_due
    limit = max(25, int(actionable_limit))
    if actionable < limit:
        return None
    return (
        "exact-detail backpressure active: "
        f"actionable backlog **{actionable}/{limit}** • pending **{health.pending}** • "
        f"non-identity due **{non_identity_due}** • verifying **{health.verifying}**"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _clean_error(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "unknown error").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
