"""Standing Home Depot penny hunts: discovery that repeats on its own.

Felix's direction (2026-11-21): "I wanted that hunt command to do auto
scans hence the word hunt vs lookup or find." Before this module, running
the penny hunt was a one-shot manual SerpApi search whose results seeded
the penny tracker; the tracker then polled the *items* it already knew,
but nothing ever re-ran the hunt *query*, so new candidates appearing
later were never discovered.

A standing hunt is a persisted (guild, user, query, ZIP/store anchor)
registration. The discovery cycle lives inside the existing Home Depot
penny cog loop (no second app): on each pass it re-runs due hunts through
the same SerpApi provider the manual hunt uses, charges the shared
``serpapi_quota_guard`` exactly like a manual run (cache hits are free),
and seeds new candidates into the penny tracker with the exact same
``seed_from_hunt_candidates`` path a manual hunt uses — dedupe included.

Cadence: once per day per hunt by default
(``SNIPERPLUG_HD_HUNT_INTERVAL_HOURS``, default 24). Credit math at that
cadence: one hunt costs at most 1 SerpApi credit per day — about 30 per
month worst case, zero on provider cache hits — comfortably inside the
quota guard's daily limit (30) and monthly safe budget (200) alongside
manual scans and the tracker's own separately-capped polls. When the
guard says no, the cycle skips that hunt and logs it; it never burns
past the guard.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sniperplug.services.hd_penny_tracker import seed_from_hunt_candidates
from sniperplug.services.movie_ticket_snowflake_store import snowflake_text


log = logging.getLogger("sniperplug.hd_standing_hunts")

HUNTS_TABLE = "hd_standing_hunts"

DEFAULT_HUNT_INTERVAL_HOURS = 24.0
DEFAULT_MAX_HUNTS_PER_CYCLE = 10


@dataclass(frozen=True)
class HdHuntConfig:
    enabled: bool = True
    interval_hours: float = DEFAULT_HUNT_INTERVAL_HOURS
    max_hunts_per_cycle: int = DEFAULT_MAX_HUNTS_PER_CYCLE

    @classmethod
    def from_env(cls) -> "HdHuntConfig":
        return cls(
            enabled=_env_bool("SNIPERPLUG_HD_HUNTS_ENABLED", default=True),
            interval_hours=max(
                1.0, _env_float("SNIPERPLUG_HD_HUNT_INTERVAL_HOURS", DEFAULT_HUNT_INTERVAL_HOURS)
            ),
            max_hunts_per_cycle=max(
                1, _env_int("SNIPERPLUG_HD_HUNT_MAX_PER_CYCLE", DEFAULT_MAX_HUNTS_PER_CYCLE)
            ),
        )


@dataclass(frozen=True)
class StandingHunt:
    hunt_id: int
    guild_id: int | None
    user_id: int
    query: str
    zip_code: str | None
    store_id: str
    active: bool
    last_run_at: datetime | None
    created_at: datetime | None


@dataclass
class HdHuntCycleSummary:
    skipped_reason: str | None = None
    active: int = 0
    due: int = 0
    ran: int = 0
    credits_used: int = 0
    seeded_new: int = 0
    seeded_existing: int = 0
    quota_skips: int = 0
    failures: int = 0

    def log_line(self) -> str:
        if self.skipped_reason:
            return f"hd standing-hunt discovery skipped: {self.skipped_reason}"
        return (
            "hd standing-hunt discovery: "
            f"active={self.active} due={self.due} ran={self.ran} "
            f"credits={self.credits_used} seeded_new={self.seeded_new} "
            f"seeded_existing={self.seeded_existing} "
            f"quota_skips={self.quota_skips} failures={self.failures}"
        )


HuntSearch = Callable[[StandingHunt], Awaitable["HuntSearchOutcome"]]


@dataclass(frozen=True)
class HuntSearchOutcome:
    """One hunt search pass: candidates plus the SerpApi credits it cost.

    ``quota_blocked`` means the shared quota guard refused the run before
    any credit was spent — the cycle counts it as a skip, not a failure.
    """

    candidates: tuple = ()
    credits_used: int = 0
    quota_blocked: bool = False


# ---------------------------------------------------------------------------
# Schema + registration


async def ensure_hd_hunts_table(db: Any) -> None:
    if bool(getattr(db, "_hd_hunts_table_ready", False)):
        return
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HUNTS_TABLE} (
            hunt_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            user_id TEXT NOT NULL,
            query TEXT NOT NULL,
            query_key TEXT NOT NULL,
            zip_code TEXT,
            store_id TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{HUNTS_TABLE}_identity "
        f"ON {HUNTS_TABLE} (guild_id, user_id, query_key, zip_code, store_id)"
    )
    await conn.commit()
    try:
        setattr(db, "_hd_hunts_table_ready", True)
    except Exception:
        pass


def hunt_query_key(query: str) -> str:
    return " ".join(str(query or "").split()).lower()


async def register_standing_hunt(
    db: Any,
    *,
    guild_id: int | None,
    user_id: int,
    query: str,
    zip_code: str | None = None,
    store_id: str = "",
    now: datetime | None = None,
) -> StandingHunt:
    """Create or reactivate the standing hunt for this exact search.

    Re-running the same hunt refreshes its anchor and marks it as just
    run (the manual pass that registered it *is* the first cycle pass),
    so the discovery cycle will not immediately spend a second credit.
    """

    await ensure_hd_hunts_table(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    stamp = now_dt.isoformat()
    cleaned_query = " ".join(str(query or "").split())
    if not cleaned_query:
        raise ValueError("A standing hunt needs a search query.")
    existing = await find_standing_hunt(
        db,
        guild_id=guild_id,
        user_id=user_id,
        query=cleaned_query,
        zip_code=zip_code,
        store_id=store_id,
    )
    if existing is not None:
        # Reactivate in place: a NULL guild_id never trips the unique
        # index's conflict path in SQLite, so dedupe cannot rely on the
        # upsert alone.
        await conn.execute(
            f"UPDATE {HUNTS_TABLE} SET query = ?, active = 1, last_run_at = ?, "
            "updated_at = ? WHERE hunt_id = ?",
            (cleaned_query, stamp, stamp, existing.hunt_id),
        )
        await conn.commit()
        refreshed = await find_standing_hunt(
            db,
            guild_id=guild_id,
            user_id=user_id,
            query=cleaned_query,
            zip_code=zip_code,
            store_id=store_id,
        )
        assert refreshed is not None
        return refreshed
    await conn.execute(
        f"""
        INSERT INTO {HUNTS_TABLE} (
            guild_id, user_id, query, query_key, zip_code, store_id,
            active, last_run_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(guild_id, user_id, query_key, zip_code, store_id)
        DO UPDATE SET
            query = excluded.query,
            active = 1,
            last_run_at = excluded.last_run_at,
            updated_at = excluded.updated_at
        """,
        (
            guild_id,
            snowflake_text(user_id),
            cleaned_query,
            hunt_query_key(cleaned_query),
            (zip_code or "").strip() or None,
            (store_id or "").strip(),
            stamp,
            stamp,
            stamp,
        ),
    )
    await conn.commit()
    hunt = await find_standing_hunt(
        db,
        guild_id=guild_id,
        user_id=user_id,
        query=cleaned_query,
        zip_code=zip_code,
        store_id=store_id,
    )
    if hunt is None:  # pragma: no cover - the upsert above just wrote it
        raise RuntimeError("Standing hunt failed its database round-trip check.")
    return hunt


async def find_standing_hunt(
    db: Any,
    *,
    guild_id: int | None,
    user_id: int,
    query: str,
    zip_code: str | None = None,
    store_id: str = "",
) -> StandingHunt | None:
    await ensure_hd_hunts_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT hunt_id, guild_id, user_id, query, zip_code, store_id,
               active, last_run_at, created_at
        FROM {HUNTS_TABLE}
        WHERE guild_id IS ? AND user_id = ? AND query_key = ?
          AND zip_code IS ? AND store_id = ?
        """,
        (
            guild_id,
            snowflake_text(user_id),
            hunt_query_key(query),
            (zip_code or "").strip() or None,
            (store_id or "").strip(),
        ),
    )
    row = await cursor.fetchone()
    return _hunt_from_row(row) if row else None


async def list_standing_hunts(
    db: Any,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
    active_only: bool = True,
    limit: int = 100,
) -> list[StandingHunt]:
    await ensure_hd_hunts_table(db)
    conn = db.require_conn()
    clauses: list[str] = []
    params: list[Any] = []
    if active_only:
        clauses.append("active = 1")
    if guild_id is not None:
        clauses.append("guild_id = ?")
        params.append(guild_id)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(snowflake_text(user_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = await conn.execute(
        f"""
        SELECT hunt_id, guild_id, user_id, query, zip_code, store_id,
               active, last_run_at, created_at
        FROM {HUNTS_TABLE} {where}
        ORDER BY hunt_id
        LIMIT ?
        """,
        (*params, int(limit)),
    )
    rows = await cursor.fetchall()
    return [_hunt_from_row(row) for row in rows]


async def stop_standing_hunt(db: Any, *, hunt_id: int, user_id: int) -> bool:
    """Deactivate one hunt owned by ``user_id``. Tracked items are untouched."""

    await ensure_hd_hunts_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"UPDATE {HUNTS_TABLE} SET active = 0, updated_at = CURRENT_TIMESTAMP "
        "WHERE hunt_id = ? AND user_id = ? AND active = 1",
        (int(hunt_id), snowflake_text(user_id)),
    )
    await conn.commit()
    return bool(getattr(cursor, "rowcount", 0))


async def _mark_hunt_run(db: Any, hunt_id: int, *, now: datetime) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"UPDATE {HUNTS_TABLE} SET last_run_at = ?, updated_at = ? WHERE hunt_id = ?",
        (now.isoformat(), now.isoformat(), int(hunt_id)),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Discovery cycle


async def run_hd_hunt_discovery_cycle(
    db: Any,
    *,
    config: HdHuntConfig | None = None,
    run_search: HuntSearch | None = None,
    now: datetime | None = None,
) -> HdHuntCycleSummary:
    """Re-run every due standing hunt once, seeding new candidates.

    ``run_search`` is injectable for tests; the default goes through the
    SerpApi provider + shared quota guard (see
    :func:`serpapi_hunt_search`). A quota-blocked hunt is a skip with a
    log line, never a burn and never a failure.
    """

    cfg = config or HdHuntConfig.from_env()
    summary = HdHuntCycleSummary()
    if not cfg.enabled:
        summary.skipped_reason = "standing hunts are turned off"
        return summary
    import os as _os

    if not _os.getenv("SERPAPI_API_KEY", "").strip():
        summary.skipped_reason = "SERPAPI_API_KEY is not set"
        return summary

    await ensure_hd_hunts_table(db)
    now_dt = _utc(now)
    hunts = await list_standing_hunts(db, active_only=True, limit=1000)
    summary.active = len(hunts)
    cutoff = now_dt - timedelta(hours=cfg.interval_hours)
    due = [
        hunt
        for hunt in hunts
        if hunt.last_run_at is None or hunt.last_run_at <= cutoff
    ]
    due.sort(key=lambda hunt: (hunt.last_run_at.isoformat() if hunt.last_run_at else "", hunt.hunt_id))
    due = due[: cfg.max_hunts_per_cycle]
    summary.due = len(due)

    search = run_search or serpapi_hunt_search
    for hunt in due:
        try:
            outcome = await search(hunt)
        except Exception:  # noqa: BLE001 - one bad hunt cannot stop the cycle.
            log.exception("hd standing hunt search failed hunt=%s", hunt.hunt_id)
            summary.failures += 1
            continue
        if outcome.quota_blocked:
            summary.quota_skips += 1
            log.info(
                "hd standing hunt skipped by SerpApi quota guard hunt=%s query=%r",
                hunt.hunt_id,
                hunt.query,
            )
            continue
        summary.ran += 1
        summary.credits_used += outcome.credits_used
        await _mark_hunt_run(db, hunt.hunt_id, now=now_dt)
        if outcome.candidates:
            try:
                seeded = await seed_from_hunt_candidates(
                    db,
                    outcome.candidates,
                    guild_id=hunt.guild_id,
                    store_id=hunt.store_id or "",
                    zip_code=hunt.zip_code,
                    now=now_dt,
                )
                summary.seeded_new += seeded.tracked_new
                summary.seeded_existing += seeded.tracked_existing
            except Exception:  # noqa: BLE001 - seeding must not stop the cycle.
                log.exception("hd standing hunt seeding failed hunt=%s", hunt.hunt_id)
                summary.failures += 1
    return summary


async def serpapi_hunt_search(hunt: StandingHunt) -> HuntSearchOutcome:
    """The default hunt search: SerpApi provider, quota-guarded, lock-free.

    Mirrors the manual hunt's spending rules exactly: the shared quota
    guard is checked before the scan, and a provider cache hit costs 0.
    The manual path's interaction scan lock does not apply here — the
    cycle itself is the single runner, and due-ness is persisted.
    """

    from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
    from sniperplug.providers.registry import provider_registry
    from sniperplug.services.quota_guard import serpapi_quota_guard

    provider = provider_registry.get("home_depot_serpapi")
    if provider is None:
        return HuntSearchOutcome(quota_blocked=True)
    health = await provider.healthcheck()
    if health.status != ProviderStatus.READY:
        return HuntSearchOutcome(quota_blocked=True)
    quota = serpapi_quota_guard.check(hunt.user_id, cost=1)
    quota_blocked = not quota.allowed
    result = await provider.scan(
        ProviderScanRequest(
            source_key="home_depot_serpapi",
            query=hunt.query,
            page=1,
            max_results=24,
            metadata={
                "store_id": hunt.store_id or "",
                "zip_code": hunt.zip_code or "",
                "requested_by": str(hunt.user_id),
                "standing_hunt_id": str(hunt.hunt_id),
                # A refused SerpApi spend no longer skips the hunt outright:
                # the direct gateway lane can serve it for free, and the
                # SerpApi fallback degrades to cache-only.
                "quota_blocked": quota_blocked,
            },
        )
    )
    credits = int(result.metadata.get("serpapi_credit", 0 if result.metadata.get("cache_hit") else 1))
    if quota_blocked and not result.candidates:
        return HuntSearchOutcome(quota_blocked=True)
    serpapi_quota_guard.record(hunt.user_id, cost=credits)
    return HuntSearchOutcome(candidates=tuple(result.candidates), credits_used=credits)


# ---------------------------------------------------------------------------
# Row helpers


def _hunt_from_row(row: Any) -> StandingHunt:
    return StandingHunt(
        hunt_id=int(_row_get(row, "hunt_id", 0)),
        guild_id=_int_or_none(_row_get(row, "guild_id", 1)),
        user_id=int(_row_get(row, "user_id", 2) or 0),
        query=str(_row_get(row, "query", 3) or ""),
        zip_code=(str(_row_get(row, "zip_code", 4)).strip() or None)
        if _row_get(row, "zip_code", 4) is not None
        else None,
        store_id=str(_row_get(row, "store_id", 5) or ""),
        active=bool(_row_get(row, "active", 6)),
        last_run_at=_parse_iso(_row_get(row, "last_run_at", 7)),
        created_at=_parse_iso(_row_get(row, "created_at", 8)),
    )


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


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
