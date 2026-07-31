from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.public_deal_posts import ensure_public_post_tables


ACTIVE_DEAL_HISTORY_RETENTION_DAYS = 30
ACTIVE_DEAL_HISTORY_MAX_ROWS_PER_GUILD = 1000


async def ensure_active_deal_history(db: Any) -> None:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_active_deal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            active_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            event_type TEXT NOT NULL,
            old_price REAL,
            new_price REAL,
            old_discount REAL,
            new_discount REAL,
            old_status TEXT,
            new_status TEXT,
            source_label TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_active_deal_history_guild_time ON guild_active_deal_history (guild_id, occurred_at DESC)"
    )
    await conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_active_deal_cache_history_update
        AFTER UPDATE OF current_price, discount, status ON guild_active_deal_cache
        WHEN
            OLD.current_price IS NOT NEW.current_price
            OR OLD.discount IS NOT NEW.discount
            OR OLD.status IS NOT NEW.status
        BEGIN
            INSERT INTO guild_active_deal_history (
                guild_id, active_key, retailer, title, event_type,
                old_price, new_price, old_discount, new_discount,
                old_status, new_status, source_label, occurred_at
            ) VALUES (
                NEW.guild_id,
                NEW.active_key,
                NEW.retailer,
                NEW.title,
                CASE
                    WHEN OLD.status != NEW.status AND NEW.status = 'stale' THEN 'marked_stale'
                    WHEN OLD.status != NEW.status AND NEW.status = 'active' THEN 'reactivated'
                    WHEN OLD.current_price IS NOT NEW.current_price AND NEW.current_price < OLD.current_price THEN 'price_drop'
                    WHEN OLD.current_price IS NOT NEW.current_price AND NEW.current_price > OLD.current_price THEN 'price_increase'
                    WHEN OLD.discount IS NOT NEW.discount AND NEW.discount IS NULL THEN 'discount_unproven'
                    WHEN OLD.discount IS NOT NEW.discount AND COALESCE(NEW.discount, 0) > COALESCE(OLD.discount, 0) THEN 'discount_improved'
                    WHEN OLD.discount IS NOT NEW.discount AND COALESCE(NEW.discount, 0) < COALESCE(OLD.discount, 0) THEN 'discount_weakened'
                    ELSE 'cache_changed'
                END,
                OLD.current_price,
                NEW.current_price,
                OLD.discount,
                NEW.discount,
                OLD.status,
                NEW.status,
                NEW.source_label,
                STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now')
            );
        END
        """
    )
    await conn.commit()


async def prune_active_deal_history(
    db: Any,
    *,
    guild_id: int | None = None,
    retention_days: int = ACTIVE_DEAL_HISTORY_RETENTION_DAYS,
    max_rows_per_guild: int = ACTIVE_DEAL_HISTORY_MAX_ROWS_PER_GUILD,
) -> int:
    await ensure_active_deal_history(db)
    conn = db.require_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat()
    if guild_id is None:
        cursor = await conn.execute("DELETE FROM guild_active_deal_history WHERE occurred_at < ?", (cutoff,))
    else:
        cursor = await conn.execute(
            "DELETE FROM guild_active_deal_history WHERE guild_id = ? AND occurred_at < ?",
            (guild_id, cutoff),
        )
        await conn.execute(
            """
            DELETE FROM guild_active_deal_history
            WHERE guild_id = ? AND id NOT IN (
                SELECT id FROM guild_active_deal_history
                WHERE guild_id = ?
                ORDER BY occurred_at DESC, id DESC
                LIMIT ?
            )
            """,
            (guild_id, guild_id, max(1, int(max_rows_per_guild))),
        )
    await conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0)


async def list_active_deal_history(
    db: Any,
    guild_id: int,
    *,
    retailer: str | None = None,
    search: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    await prune_active_deal_history(db, guild_id=guild_id)
    conn = db.require_conn()
    filters = ["guild_id = ?"]
    params: list[Any] = [guild_id]
    if retailer:
        filters.append("retailer = ?")
        params.append(str(retailer).strip().lower())
    clean_search = " ".join(str(search or "").split())
    if clean_search:
        pattern = f"%{clean_search.lower()}%"
        filters.append("(LOWER(title) LIKE ? OR LOWER(active_key) LIKE ? OR LOWER(event_type) LIKE ?)")
        params.extend([pattern, pattern, pattern])
    params.append(max(1, min(int(limit), 25)))
    cursor = await conn.execute(
        f"""
        SELECT id, guild_id, active_key, retailer, title, event_type,
               old_price, new_price, old_discount, new_discount,
               old_status, new_status, source_label, occurred_at
        FROM guild_active_deal_history
        WHERE {' AND '.join(filters)}
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]
