from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


TOMBSTONE_TABLE = "guild_setup_ghost_tombstones"


async def ensure_ghost_tombstone_table(conn: Any) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TOMBSTONE_TABLE} (
            guild_id INTEGER PRIMARY KEY,
            reason TEXT NOT NULL DEFAULT 'not_in_live_guild_cache',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
        """
    )


async def load_ghost_tombstones(conn: Any) -> set[int]:
    await ensure_ghost_tombstone_table(conn)
    cursor = await conn.execute(f"SELECT guild_id FROM {TOMBSTONE_TABLE}")
    rows = await cursor.fetchall()
    output: set[int] = set()
    for row in rows:
        value = _row_value(row, "guild_id", 0)
        try:
            output.add(int(value))
        except (TypeError, ValueError):
            continue
    return output


async def mark_ghost_tombstones(
    conn: Any,
    guild_ids: Iterable[int],
    *,
    reason: str = "not_in_live_guild_cache",
) -> int:
    ids = sorted({int(guild_id) for guild_id in guild_ids})
    if not ids:
        return 0
    await ensure_ghost_tombstone_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    for guild_id in ids:
        await conn.execute(
            f"""
            INSERT INTO {TOMBSTONE_TABLE}
                (guild_id, reason, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                reason = excluded.reason,
                last_seen_at = excluded.last_seen_at
            """,
            (guild_id, str(reason or "ghost")[:120], now, now),
        )
    return len(ids)


async def clear_live_ghost_tombstones(conn: Any, live_guild_ids: Iterable[int]) -> int:
    ids = sorted({int(guild_id) for guild_id in live_guild_ids})
    if not ids:
        return 0
    await ensure_ghost_tombstone_table(conn)
    changed = 0
    for guild_id in ids:
        cursor = await conn.execute(
            f"DELETE FROM {TOMBSTONE_TABLE} WHERE guild_id = ?",
            (guild_id,),
        )
        rowcount = getattr(cursor, "rowcount", None)
        if isinstance(rowcount, int) and rowcount > 0:
            changed += rowcount
    return changed


def _row_value(row: Any, key: str, index: int) -> Any:
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
