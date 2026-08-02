from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sniperplug.services.discord_snowflake import snowflake_text


TOMBSTONE_TABLE = "guild_setup_ghost_tombstones"


async def ensure_ghost_tombstone_table(conn: Any) -> None:
    """Create and durably commit the tombstone table.

    Tombstone helpers own their transaction boundaries so callers never depend
    on an unrelated later config write to make quarantine state persistent.
    """

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
    await conn.commit()


async def load_ghost_tombstones(
    conn: Any,
    *,
    guild_ids: Iterable[int] | None = None,
) -> set[int]:
    await ensure_ghost_tombstone_table(conn)
    # Force SQLite/libSQL to serialize the 64-bit Discord snowflake as text.
    # A numeric wire decoder may otherwise round IDs above 2**53.
    ids = sorted({int(guild_id) for guild_id in guild_ids or ()})
    sql = f"SELECT CAST(guild_id AS TEXT) AS guild_id FROM {TOMBSTONE_TABLE}"
    params: tuple[str, ...] = ()
    if ids:
        placeholders = ", ".join("?" for _ in ids)
        sql += f" WHERE CAST(guild_id AS TEXT) IN ({placeholders})"
        params = tuple(snowflake_text(guild_id) for guild_id in ids)
    cursor = await conn.execute(sql, params)
    rows = await cursor.fetchall()
    output: set[int] = set()
    for row in rows:
        value = _row_value(row, "guild_id", 0)
        try:
            output.add(int(str(value)))
        except (TypeError, ValueError):
            continue
    return output


async def mark_ghost_tombstones(
    conn: Any,
    guild_ids: Iterable[int],
    *,
    reason: str = "not_in_live_guild_cache",
) -> int:
    """Upsert tombstones with exact decimal text parameters and commit."""

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
            (snowflake_text(guild_id), str(reason or "ghost")[:120], now, now),
        )
    await conn.commit()
    return len(ids)


async def clear_live_ghost_tombstones(conn: Any, live_guild_ids: Iterable[int]) -> int:
    """Release only exact rejoined-guild tombstones and commit.

    Binding a live snowflake as a numeric transport value can round it to a
    different guild ID and accidentally delete that ghost's quarantine row.
    Decimal text plus CAST comparison prevents that recurrence permanently.
    """

    ids = sorted({int(guild_id) for guild_id in live_guild_ids})
    if not ids:
        return 0
    await ensure_ghost_tombstone_table(conn)
    changed = 0
    for guild_id in ids:
        cursor = await conn.execute(
            f"DELETE FROM {TOMBSTONE_TABLE} "
            "WHERE CAST(guild_id AS TEXT) = ?",
            (snowflake_text(guild_id),),
        )
        rowcount = getattr(cursor, "rowcount", None)
        if isinstance(rowcount, int) and rowcount > 0:
            changed += rowcount
    await conn.commit()
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
