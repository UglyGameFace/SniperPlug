from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


WALMART_RECHECK_AUDIT_RETENTION_DAYS = 30
WALMART_RECHECK_AUDIT_MAX_ROWS_PER_GUILD = 2000
WALMART_RECHECK_AUDIT_PRUNE_BATCH_SIZE = 500


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
        "CREATE INDEX IF NOT EXISTS idx_walmart_recheck_audit_guild_time "
        "ON guild_walmart_recheck_audit (guild_id, occurred_at DESC, id DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_walmart_recheck_audit_occurred_cleanup "
        "ON guild_walmart_recheck_audit (occurred_at, id)"
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
    batch_size: int = WALMART_RECHECK_AUDIT_PRUNE_BATCH_SIZE,
) -> int:
    """Prune audit rows with bounded indexed reads.

    Global startup maintenance only seeks an old-row batch by timestamp. Per-
    guild cap enforcement remains available on explicit history access, where
    the guild/time index makes the operation selective.
    """
    await ensure_walmart_recheck_audit(db)
    conn = db.require_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat()
    safe_batch = max(1, min(int(batch_size), 5_000))
    deleted = 0

    old_ids = await _select_audit_ids(
        conn,
        guild_id=guild_id,
        cutoff=cutoff,
        limit=safe_batch,
    )
    deleted += await _delete_audit_ids(conn, old_ids)

    if guild_id is not None:
        overflow_ids = await _select_overflow_ids(
            conn,
            guild_id=int(guild_id),
            keep=max(1, int(max_rows_per_guild)),
            limit=safe_batch,
        )
        deleted += await _delete_audit_ids(conn, overflow_ids)

    if deleted:
        await conn.commit()
    return deleted


async def _select_audit_ids(
    conn: Any,
    *,
    guild_id: int | None,
    cutoff: str,
    limit: int,
) -> list[int]:
    if guild_id is None:
        cursor = await conn.execute(
            """
            SELECT id
            FROM guild_walmart_recheck_audit
            WHERE occurred_at < ?
            ORDER BY occurred_at ASC, id ASC
            LIMIT ?
            """,
            (cutoff, limit),
        )
    else:
        cursor = await conn.execute(
            """
            SELECT id
            FROM guild_walmart_recheck_audit
            WHERE guild_id = ? AND occurred_at < ?
            ORDER BY occurred_at ASC, id ASC
            LIMIT ?
            """,
            (int(guild_id), cutoff, limit),
        )
    return _ids_from_rows(await cursor.fetchall())


async def _select_overflow_ids(
    conn: Any,
    *,
    guild_id: int,
    keep: int,
    limit: int,
) -> list[int]:
    cursor = await conn.execute(
        """
        SELECT id
        FROM guild_walmart_recheck_audit
        WHERE guild_id = ?
        ORDER BY occurred_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (guild_id, limit, keep),
    )
    return _ids_from_rows(await cursor.fetchall())


async def _delete_audit_ids(conn: Any, ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    await conn.execute(
        f"DELETE FROM guild_walmart_recheck_audit WHERE id IN ({placeholders})",
        tuple(ids),
    )
    return len(ids)


def _ids_from_rows(rows: list[Any]) -> list[int]:
    ids: list[int] = []
    for row in rows:
        value = _row_value(row, "id", 0)
        try:
            item_id = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        ids.append(item_id)
    return ids


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        return getattr(row, key, None)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
