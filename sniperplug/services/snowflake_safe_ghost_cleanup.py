from __future__ import annotations

import logging
from typing import Any

from sniperplug.services.discord_snowflake import snowflake_text


log = logging.getLogger("sniperplug.setup_self_heal")

CONFIG_TABLES = (
    "guild_public_alert_settings",
    "guild_retailer_auto_scan_settings",
    "guild_retailer_auto_scan_runs",
    "guild_alert_channels",
    "guild_public_deal_posts",
    "guild_active_deal_cache",
    "alert_dedupe",
)


async def discover_ghost_ids(conn: Any, live_guild_ids: set[int]) -> set[int]:
    ghost_ids: set[int] = set()
    for table in CONFIG_TABLES:
        try:
            cursor = await conn.execute(
                f"SELECT DISTINCT CAST(guild_id AS TEXT) AS guild_id FROM {table}"
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            if not _missing_table_error(exc):
                log.warning("Ghost guild discovery skipped table=%s error=%s", table, exc)
            continue
        for row in rows:
            guild_id = _guild_id_from_row(row)
            if guild_id is not None and guild_id not in live_guild_ids:
                ghost_ids.add(guild_id)
    return ghost_ids


async def delete_ghost_rows_once(conn: Any, ghost_ids: set[int]) -> list[str]:
    """Delete exact ghost snowflakes without numeric transport rounding."""

    failures: list[str] = []
    for guild_id in sorted(ghost_ids):
        guild_param = snowflake_text(guild_id)
        for table in reversed(CONFIG_TABLES):
            try:
                await conn.execute(
                    f"DELETE FROM {table} WHERE CAST(guild_id AS TEXT) = ?",
                    (guild_param,),
                )
            except Exception as exc:
                if _missing_table_error(exc):
                    continue
                failures.append(f"{table}:{guild_param}:{type(exc).__name__}:{exc}")
    return failures


async def remaining_ghost_ids(conn: Any, ghost_ids: set[int]) -> set[int]:
    """Verify deletion using exact text comparison on both sides."""

    remaining: set[int] = set()
    for guild_id in sorted(ghost_ids):
        guild_param = snowflake_text(guild_id)
        for table in CONFIG_TABLES:
            try:
                cursor = await conn.execute(
                    f"SELECT 1 AS present FROM {table} "
                    "WHERE CAST(guild_id AS TEXT) = ? LIMIT 1",
                    (guild_param,),
                )
                row = await cursor.fetchone()
            except Exception as exc:
                if _missing_table_error(exc):
                    continue
                log.warning(
                    "Ghost guild verification failed table=%s guild=%s error=%s",
                    table,
                    guild_param,
                    exc,
                )
                remaining.add(guild_id)
                break
            if row:
                remaining.add(guild_id)
                break
    return remaining


def _missing_table_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "no such table" in text
        or "does not exist" in text
        or "unknown table" in text
    )


def _guild_id_from_row(row: Any) -> int | None:
    values: list[Any] = []
    try:
        values.append(row["guild_id"])
    except Exception:
        pass
    try:
        values.append(row[0])
    except Exception:
        pass
    values.append(getattr(row, "guild_id", None))
    for value in values:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            continue
    return None
