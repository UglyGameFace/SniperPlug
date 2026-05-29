from __future__ import annotations

from sniperplug.models.deal import utc_now_iso


DEFAULT_STARTING_DEAL_PERCENT = 40
MIN_STARTING_DEAL_PERCENT = 0
MAX_STARTING_DEAL_PERCENT = 95


def normalize_starting_deal_percent(value: int | float | str | None, *, fallback: int = DEFAULT_STARTING_DEAL_PERCENT) -> int:
    try:
        percent = int(float(value))
    except (TypeError, ValueError):
        percent = int(fallback)
    return max(MIN_STARTING_DEAL_PERCENT, min(MAX_STARTING_DEAL_PERCENT, percent))


async def ensure_deal_threshold_storage(db) -> None:
    if db is None:
        return
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            deals_channel_id INTEGER,
            min_discount_percent REAL NOT NULL DEFAULT 40,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor = await conn.execute("PRAGMA table_info(guild_settings)")
    columns = {str(row["name"]) for row in await cursor.fetchall()}
    if "min_discount_percent" not in columns:
        await conn.execute("ALTER TABLE guild_settings ADD COLUMN min_discount_percent REAL NOT NULL DEFAULT 40")
    await conn.commit()


async def get_starting_deal_percent(db, guild_id: int | None, *, fallback: int = DEFAULT_STARTING_DEAL_PERCENT) -> int:
    if db is None or guild_id is None:
        return normalize_starting_deal_percent(fallback)
    await ensure_deal_threshold_storage(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT min_discount_percent FROM guild_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return normalize_starting_deal_percent(fallback)
    return normalize_starting_deal_percent(row["min_discount_percent"], fallback=fallback)


async def set_starting_deal_percent(db, guild_id: int, percent: int | float | str) -> int:
    await ensure_deal_threshold_storage(db)
    safe_percent = normalize_starting_deal_percent(percent)
    conn = db.require_conn()
    now = utc_now_iso()
    await conn.execute(
        """
        INSERT INTO guild_settings (guild_id, min_discount_percent, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            min_discount_percent = excluded.min_discount_percent,
            updated_at = excluded.updated_at
        """,
        (guild_id, safe_percent, now, now),
    )
    await conn.commit()
    return safe_percent
