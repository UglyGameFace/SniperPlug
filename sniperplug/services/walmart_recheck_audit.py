from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


WALMART_RECHECK_AUDIT_RETENTION_DAYS = 30
WALMART_RECHECK_AUDIT_MAX_ROWS_PER_GUILD = 2000


async def ensure_walmart_recheck_audit(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_walmart_recheck_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            active_key TEXT NOT NULL,
            item_id TEXT,
            title TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            actor_user_id INTEGER,
            actor_name TEXT,
            result_status TEXT NOT NULL,
            reused INTEGER NOT NULL DEFAULT 0,
            old_price REAL,
            new_price REAL,
            old_discount REAL,
            new_discount REAL,
            reference_price REAL,
            cache_status TEXT,
            message TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_walmart_recheck_audit_guild_time ON guild_walmart_recheck_audit (guild_id, occurred_at DESC)"
    )
    await conn.commit()


async def record_walmart_recheck_attempt(
    db: Any,
    guild_id: int,
    row: dict[str, Any],
    result: Any,
    *,
    trigger_source: str,
    actor_user_id: int | None = None,
    actor_name: str | None = None,
) -> None:
    await ensure_walmart_recheck_audit(db)
    conn = db.require_conn()
    await conn.execute(
        """
        INSERT INTO guild_walmart_recheck_audit (
            guild_id, active_key, item_id, title, trigger_source,
            actor_user_id, actor_name, result_status, reused,
            old_price, new_price, old_discount, new_discount,
            reference_price, cache_status, message, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guild_id),
            str(row.get("active_key") or "unknown")[:500],
            str(getattr(result, "item_id", None) or "")[:100] or None,
            str(row.get("title") or "Unknown Walmart item")[:500],
            str(trigger_source or "unknown")[:80],
            int(actor_user_id) if actor_user_id is not None else None,
            str(actor_name or "")[:160] or None,
            str(getattr(result, "status", None) or "unknown")[:80],
            1 if bool(getattr(result, "reused", False)) else 0,
            _float_or_none(getattr(result, "old_price", None)),
            _float_or_none(getattr(result, "current_price", None)),
            _float_or_none(getattr(result, "old_discount", None)),
            _float_or_none(getattr(result, "current_discount", None)),
            _float_or_none(getattr(result, "reference_price", None)),
            str(getattr(result, "cache_status", None) or "")[:40] or None,
            str(getattr(result, "message", None) or "")[:1200],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    await conn.commit()


async def list_walmart_recheck_audit(
    db: Any,
    guild_id: int,
    *,
    search: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    await prune_walmart_recheck_audit(db, guild_id=guild_id)
    conn = db.require_conn()
    filters = ["guild_id = ?"]
    params: list[Any] = [int(guild_id)]
    clean_search = " ".join(str(search or "").split()).lower()
    if clean_search:
        pattern = f"%{clean_search}%"
        filters.append(
            "(LOWER(title) LIKE ? OR LOWER(active_key) LIKE ? OR LOWER(result_status) LIKE ? OR LOWER(trigger_source) LIKE ?)"
        )
        params.extend([pattern, pattern, pattern, pattern])
    params.append(max(1, min(int(limit), 25)))
    cursor = await conn.execute(
        f"""
        SELECT id, guild_id, active_key, item_id, title, trigger_source,
               actor_user_id, actor_name, result_status, reused,
               old_price, new_price, old_discount, new_discount,
               reference_price, cache_status, message, occurred_at
        FROM guild_walmart_recheck_audit
        WHERE {' AND '.join(filters)}
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def prune_walmart_recheck_audit(
    db: Any,
    *,
    guild_id: int | None = None,
    retention_days: int = WALMART_RECHECK_AUDIT_RETENTION_DAYS,
    max_rows_per_guild: int = WALMART_RECHECK_AUDIT_MAX_ROWS_PER_GUILD,
) -> int:
    await ensure_walmart_recheck_audit(db)
    conn = db.require_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat()
    deleted = 0
    if guild_id is None:
        cursor = await conn.execute("DELETE FROM guild_walmart_recheck_audit WHERE occurred_at < ?", (cutoff,))
        deleted += _row_count(cursor)
        cursor = await conn.execute(
            """
            DELETE FROM guild_walmart_recheck_audit
            WHERE id IN (
                SELECT audit.id
                FROM guild_walmart_recheck_audit AS audit
                WHERE (
                    SELECT COUNT(*)
                    FROM guild_walmart_recheck_audit AS newer
                    WHERE newer.guild_id = audit.guild_id
                      AND (
                          newer.occurred_at > audit.occurred_at
                          OR (newer.occurred_at = audit.occurred_at AND newer.id > audit.id)
                      )
                ) >= ?
            )
            """,
            (max(1, int(max_rows_per_guild)),),
        )
        deleted += _row_count(cursor)
    else:
        cursor = await conn.execute(
            "DELETE FROM guild_walmart_recheck_audit WHERE guild_id = ? AND occurred_at < ?",
            (int(guild_id), cutoff),
        )
        deleted += _row_count(cursor)
        cursor = await conn.execute(
            """
            DELETE FROM guild_walmart_recheck_audit
            WHERE guild_id = ? AND id NOT IN (
                SELECT id FROM guild_walmart_recheck_audit
                WHERE guild_id = ?
                ORDER BY occurred_at DESC, id DESC
                LIMIT ?
            )
            """,
            (int(guild_id), int(guild_id), max(1, int(max_rows_per_guild))),
        )
        deleted += _row_count(cursor)
    await conn.commit()
    return deleted


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _row_count(cursor: Any) -> int:
    try:
        return max(0, int(getattr(cursor, "rowcount", 0) or 0))
    except (TypeError, ValueError):
        return 0
