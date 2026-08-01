from __future__ import annotations

import asyncio
from typing import Any

from sniperplug.services.movie_ticket_drops import (
    MovieTicketConfig,
    MovieTicketStore,
    utc_now_iso,
)


class SnowflakeSafeMovieTicketStore(MovieTicketStore):
    """Persist Discord guild/channel snowflakes as exact decimal text.

    Discord snowflakes are opaque identifiers. Some remote SQLite/libSQL result
    paths can coerce large numeric values in ways that lose precision. Keeping
    the configuration IDs in a dedicated TEXT table guarantees an exact
    round-trip on both SQLite and Turso.
    """

    def __init__(self, db: Any):
        super().__init__(db)
        self._snowflake_schema_ready = False
        self._snowflake_schema_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        await super().ensure_schema()
        if self._snowflake_schema_ready:
            return
        async with self._snowflake_schema_lock:
            if self._snowflake_schema_ready:
                return
            conn = self.db.require_conn()
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS movie_ticket_config_v2 (
                    guild_id TEXT PRIMARY KEY,
                    alert_channel_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_movie_ticket_config_v2_enabled
                    ON movie_ticket_config_v2(enabled, guild_id);
                """
            )
            await conn.commit()
            self._snowflake_schema_ready = True

    async def get_config(self, guild_id: int) -> MovieTicketConfig:
        await self.ensure_schema()
        guild_text = snowflake_text(guild_id)
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT guild_id, alert_channel_id, enabled
            FROM movie_ticket_config_v2
            WHERE guild_id = ?
            """,
            (guild_text,),
        )
        row = await cursor.fetchone()
        if not row:
            return MovieTicketConfig(guild_id=int(guild_text))

        stored_guild = snowflake_int(_row_value(row, "guild_id"))
        stored_channel = optional_snowflake_int(_row_value(row, "alert_channel_id"))
        return MovieTicketConfig(
            guild_id=stored_guild,
            alert_channel_id=stored_channel,
            enabled=bool(_row_value(row, "enabled")),
        )

    async def save_config(self, config: MovieTicketConfig) -> None:
        await self.ensure_schema()
        guild_text = snowflake_text(config.guild_id)
        channel_text = optional_snowflake_text(config.alert_channel_id)
        now = utc_now_iso()
        conn = self.db.require_conn()
        await conn.execute(
            """
            INSERT INTO movie_ticket_config_v2 (
                guild_id, alert_channel_id, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                alert_channel_id = excluded.alert_channel_id,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                guild_text,
                channel_text,
                1 if config.enabled else 0,
                now,
                now,
            ),
        )
        await conn.commit()

        # Do not tell a server owner setup succeeded unless the exact snowflake
        # survived the database round trip.
        verified = await self.get_config(int(guild_text))
        if verified.guild_id != int(guild_text):
            raise RuntimeError("Movie alert guild ID failed its exact database round-trip check.")
        if verified.alert_channel_id != config.alert_channel_id:
            raise RuntimeError("Movie alert channel ID failed its exact database round-trip check.")
        if verified.enabled != bool(config.enabled):
            raise RuntimeError("Movie alert enabled state failed its database round-trip check.")

    async def list_enabled_configs(self) -> list[MovieTicketConfig]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT guild_id, alert_channel_id, enabled
            FROM movie_ticket_config_v2
            WHERE enabled = 1 AND alert_channel_id IS NOT NULL
            """
        )
        rows = await cursor.fetchall()
        configs: list[MovieTicketConfig] = []
        for row in rows:
            try:
                configs.append(
                    MovieTicketConfig(
                        guild_id=snowflake_int(_row_value(row, "guild_id")),
                        alert_channel_id=snowflake_int(_row_value(row, "alert_channel_id")),
                        enabled=True,
                    )
                )
            except (TypeError, ValueError):
                # A malformed row must never route an alert to a guessed ID.
                continue
        return configs


def snowflake_text(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid Discord snowflakes.")
    text = str(value or "").strip()
    if not text.isdecimal():
        raise ValueError(f"Invalid Discord snowflake: {value!r}")
    parsed = int(text)
    if parsed <= 0:
        raise ValueError(f"Invalid Discord snowflake: {value!r}")
    return str(parsed)


def optional_snowflake_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return snowflake_text(value)


def snowflake_int(value: Any) -> int:
    return int(snowflake_text(value))


def optional_snowflake_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return snowflake_int(value)


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
