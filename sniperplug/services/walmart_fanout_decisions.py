from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


DECISION_TABLE = "walmart_guild_fanout_decisions"
DECISION_RETENTION_DAYS = 31
DECISION_UPSERT_CHUNK_SIZE = 100
_SCHEMA_CONNECTIONS: dict[int, Any] = {}


@dataclass(frozen=True)
class WalmartGuildFanoutDecision:
    event_key: str
    guild_id: int
    outcome: str
    threshold: int
    detail: str
    decided_at: str


async def ensure_walmart_fanout_decision_table(db: Any) -> None:
    """Install the small provenance schema once per live DB connection.

    The caller owns the surrounding commit so this helper composes with fanout's
    existing one-commit schema initialization and with the bulk decision upsert.
    """

    conn = db.require_conn()
    if _SCHEMA_CONNECTIONS.get(id(conn)) is conn:
        return
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DECISION_TABLE} (
            event_key TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            threshold INTEGER NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL,
            PRIMARY KEY (event_key, guild_id)
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{DECISION_TABLE}_guild_time "
        f"ON {DECISION_TABLE} (guild_id, decided_at DESC)"
    )
    _SCHEMA_CONNECTIONS[id(conn)] = conn


async def record_walmart_fanout_decisions(
    db: Any,
    decisions: Iterable[WalmartGuildFanoutDecision],
) -> int:
    """Persist final per-guild public fanout outcomes in bounded bulk writes."""

    rows = list(decisions)
    if not rows:
        return 0
    await ensure_walmart_fanout_decision_table(db)
    conn = db.require_conn()
    written = 0
    for offset in range(0, len(rows), DECISION_UPSERT_CHUNK_SIZE):
        chunk = rows[offset : offset + DECISION_UPSERT_CHUNK_SIZE]
        placeholders = ",".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
        params: tuple[Any, ...] = tuple(
            value
            for decision in chunk
            for value in (
                str(decision.event_key),
                str(int(decision.guild_id)),
                _clean(decision.outcome, 80),
                int(decision.threshold),
                _clean(decision.detail, 600),
                str(decision.decided_at),
            )
        )
        await conn.execute(
            f"""
            INSERT INTO {DECISION_TABLE} (
                event_key, guild_id, outcome, threshold, detail, decided_at
            ) VALUES {placeholders}
            ON CONFLICT(event_key, guild_id) DO UPDATE SET
                outcome = excluded.outcome,
                threshold = excluded.threshold,
                detail = excluded.detail,
                decided_at = excluded.decided_at
            """,
            params,
        )
        written += len(chunk)
    await conn.commit()
    return written


async def load_walmart_fanout_decisions(
    db: Any,
    *,
    guild_id: int,
    event_keys: Iterable[str],
) -> dict[str, WalmartGuildFanoutDecision]:
    """Read historical outcomes for inspected events without mutating storage.

    Deployments can briefly run health before fanout has created the new table.
    Treat only that expected missing-table case as an empty legacy history; other
    database errors remain visible to the caller.
    """

    keys = tuple(dict.fromkeys(str(key or "") for key in event_keys if str(key or "")))
    if not keys:
        return {}
    conn = db.require_conn()
    result: dict[str, WalmartGuildFanoutDecision] = {}
    for offset in range(0, len(keys), 250):
        chunk = keys[offset : offset + 250]
        placeholders = ",".join("?" for _ in chunk)
        try:
            cursor = await conn.execute(
                f"""
                SELECT event_key, guild_id, outcome, threshold, detail, decided_at
                FROM {DECISION_TABLE}
                WHERE guild_id = ? AND event_key IN ({placeholders})
                """,
                (str(int(guild_id)), *chunk),
            )
        except Exception as exc:
            if "no such table" in str(exc).lower() and DECISION_TABLE in str(exc):
                return {}
            raise
        for row in await cursor.fetchall():
            event_key = str(_row_get(row, "event_key", 0) or "")
            if not event_key:
                continue
            result[event_key] = WalmartGuildFanoutDecision(
                event_key=event_key,
                guild_id=_as_int(_row_get(row, "guild_id", 1)),
                outcome=str(_row_get(row, "outcome", 2) or ""),
                threshold=_as_int(_row_get(row, "threshold", 3)),
                detail=str(_row_get(row, "detail", 4) or ""),
                decided_at=str(_row_get(row, "decided_at", 5) or ""),
            )
    return result


async def prune_walmart_fanout_decisions(db: Any, *, limit: int = 500) -> int:
    """Delete one bounded age-expired decision batch."""

    await ensure_walmart_fanout_decision_table(db)
    conn = db.require_conn()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=DECISION_RETENTION_DAYS)
    ).isoformat()
    cursor = await conn.execute(
        f"""
        SELECT event_key, guild_id
        FROM {DECISION_TABLE}
        WHERE decided_at < ?
        ORDER BY decided_at ASC
        LIMIT ?
        """,
        (cutoff, max(1, min(int(limit), 5000))),
    )
    rows = list(await cursor.fetchall())
    if not rows:
        return 0
    pairs = [
        (str(_row_get(row, "event_key", 0) or ""), str(_row_get(row, "guild_id", 1) or ""))
        for row in rows
    ]
    pairs = [pair for pair in pairs if pair[0] and pair[1]]
    if not pairs:
        return 0
    placeholders = ",".join("(?, ?)" for _ in pairs)
    params = tuple(value for pair in pairs for value in pair)
    await conn.execute(
        f"""
        DELETE FROM {DECISION_TABLE}
        WHERE (event_key, guild_id) IN ({placeholders})
        """,
        params,
    )
    await conn.commit()
    return len(pairs)


def classify_public_fanout_result(
    result: Any,
    *,
    discount: float | None,
    threshold: int,
) -> tuple[str, str]:
    """Convert one actual posting result into durable historical provenance."""

    if int(getattr(result, "posted", 0) or 0) > 0:
        return "posted", "posted to configured public channel"
    if int(getattr(result, "skipped_recent_alert_duplicate", 0) or 0) > 0:
        return "recent_duplicate", "suppressed by recent public-alert duplicate guard"
    if int(getattr(result, "skipped_reserved_duplicate", 0) or 0) > 0:
        return "reserved_duplicate", "suppressed by durable reservation/post duplicate guard"
    if int(getattr(result, "skipped_disabled", 0) or 0) > 0:
        return "disabled", "public delivery was disabled or had no configured destination"
    if int(getattr(result, "skipped_wrong_retailer", 0) or 0) > 0:
        return "wrong_retailer", "Walmart was not enabled for this public destination"
    if discount is not None and float(discount) < int(threshold):
        return "below_threshold", f"below historical {int(threshold)}% public threshold"
    if int(getattr(result, "skipped_not_alertable", 0) or 0) > 0:
        reasons = tuple(getattr(result, "not_alertable_reasons", ()) or ())
        category_reason = next(
            (str(reason) for reason in reasons if str(reason).startswith("category_suppress:")),
            "",
        )
        if category_reason:
            return "category_muted", _clean(category_reason, 600)
        detail = ", ".join(str(reason) for reason in reasons if str(reason))
        return "quality_blocked", _clean(detail or "public proof/quality guard rejected event", 600)
    errors = tuple(getattr(result, "errors", ()) or ())
    if errors:
        return "error", _clean(" | ".join(str(error) for error in errors), 600)
    return "unexplained", "fanout returned no post, skip, or explicit error outcome"


def decision_now(
    *,
    event_key: str,
    guild_id: int,
    outcome: str,
    threshold: int,
    detail: str,
) -> WalmartGuildFanoutDecision:
    return WalmartGuildFanoutDecision(
        event_key=str(event_key),
        guild_id=int(guild_id),
        outcome=_clean(outcome, 80),
        threshold=int(threshold),
        detail=_clean(detail, 600),
        decided_at=datetime.now(timezone.utc).isoformat(),
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


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[: max(0, int(limit))]
