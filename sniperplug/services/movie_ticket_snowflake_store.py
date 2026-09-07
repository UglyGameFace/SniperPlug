from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sniperplug.services.movie_ticket_drops import (
    DELIVERY_RESERVATION_TTL,
    MovieTicketConfig,
    MovieTicketDrop,
    MovieTicketStore,
    _drop_from_row,
    utc_now_iso,
)
from sniperplug.services.movie_ticket_recovery import (
    MOVIE_DELIVERABLE_CLASSIFICATIONS,
    movie_source_cache_is_fresh,
)


async def ensure_movie_ticket_snowflake_tables(db: Any) -> None:
    """Create the exact-text Discord-ID tables shared by movie delivery/feedback."""

    conn = db.require_conn()
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS movie_ticket_config_v2 (
            guild_id TEXT PRIMARY KEY,
            alert_channel_id TEXT,
            enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS movie_ticket_deliveries_v2 (
            guild_id TEXT NOT NULL,
            drop_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'reserved',
            reserved_at TEXT NOT NULL,
            posted_at TEXT,
            message_id TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (guild_id, drop_id)
        );

        CREATE TABLE IF NOT EXISTS movie_ticket_feedback_v2 (
            guild_id TEXT NOT NULL,
            drop_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, drop_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_movie_ticket_config_v2_enabled
            ON movie_ticket_config_v2(enabled, guild_id);
        CREATE INDEX IF NOT EXISTS idx_movie_ticket_deliveries_v2_guild_state
            ON movie_ticket_deliveries_v2(guild_id, state, posted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_movie_ticket_delivery_v2_message
            ON movie_ticket_deliveries_v2(guild_id, message_id);
        CREATE INDEX IF NOT EXISTS idx_movie_ticket_feedback_v2_counts
            ON movie_ticket_feedback_v2(guild_id, drop_id, result);
        """
    )
    await conn.commit()


class SnowflakeSafeMovieTicketStore(MovieTicketStore):
    """Production movie store with exact Discord IDs and freshness-safe results.

    Config, delivery, message, and guild snowflakes are kept as decimal TEXT so
    the remote libSQL result path cannot round them. Durable source rows are kept
    even during upstream outages, while user-facing ``list_active_drops`` hides
    caches whose source has not been successfully confirmed recently.
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
            await ensure_movie_ticket_snowflake_tables(self.db)
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

    async def replace_active_drops(
        self,
        source_key: str,
        drops: tuple[MovieTicketDrop, ...],
    ) -> None:
        """Persist every classification the registered runtime can safely deliver."""

        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        active_ids: list[str] = []
        for drop in drops:
            if (
                drop.source_key != source_key
                or drop.classification not in MOVIE_DELIVERABLE_CLASSIFICATIONS
            ):
                continue
            active_ids.append(drop.drop_id)
            await conn.execute(
                """
                INSERT INTO movie_ticket_drops (
                    drop_id, source_key, source_label, title, code, classification, ticket_limit,
                    offer_url, validity_text, restrictions_json, raw_text,
                    first_seen_at, last_seen_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(drop_id) DO UPDATE SET
                    source_label = excluded.source_label,
                    title = excluded.title,
                    code = excluded.code,
                    classification = excluded.classification,
                    ticket_limit = excluded.ticket_limit,
                    offer_url = excluded.offer_url,
                    validity_text = excluded.validity_text,
                    restrictions_json = excluded.restrictions_json,
                    raw_text = excluded.raw_text,
                    last_seen_at = excluded.last_seen_at,
                    active = 1
                """,
                (
                    drop.drop_id,
                    drop.source_key,
                    drop.source_label,
                    drop.title,
                    drop.code,
                    drop.classification,
                    max(1, int(drop.ticket_limit)),
                    drop.offer_url,
                    drop.validity_text,
                    json.dumps(list(drop.restrictions), ensure_ascii=False),
                    drop.raw_text,
                    now,
                    now,
                ),
            )

        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            await conn.execute(
                f"UPDATE movie_ticket_drops SET active = 0 WHERE source_key = ? AND drop_id NOT IN ({placeholders})",
                (source_key, *active_ids),
            )
        else:
            await conn.execute(
                "UPDATE movie_ticket_drops SET active = 0 WHERE source_key = ?",
                (source_key,),
            )
        await conn.commit()

    async def list_active_drops(
        self,
        *,
        limit: int = 25,
        include_stale: bool = False,
    ) -> list[MovieTicketDrop]:
        """List deliverable rows, hiding stale source caches by default.

        ``include_stale`` exists only for an internal HTTP-304 recovery path. A
        successful 304 re-confirms the source immediately afterward, so the same
        durable rows can become current again without making stale data public.
        """

        await self.ensure_schema()
        conn = self.db.require_conn()
        requested_limit = max(1, min(250, int(limit)))
        raw_limit = min(500, max(100, requested_limit * 4))
        cursor = await conn.execute(
            """
            SELECT drop_id, source_key, source_label, title, code, classification, ticket_limit,
                   offer_url, validity_text, restrictions_json, raw_text,
                   first_seen_at, last_seen_at, active
            FROM movie_ticket_drops
            WHERE active = 1
              AND classification IN ('public_reusable', 'local_screening')
            ORDER BY last_seen_at DESC, title ASC
            LIMIT ?
            """,
            (raw_limit,),
        )
        rows = await cursor.fetchall()
        drops = [_drop_from_row(row) for row in rows]
        if include_stale:
            return drops[:requested_limit]

        states: dict[str, Any] = {}
        fresh: list[MovieTicketDrop] = []
        for drop in drops:
            state = states.get(drop.source_key)
            if state is None:
                state = await self.get_source_state(drop.source_key)
                states[drop.source_key] = state
            if not movie_source_cache_is_fresh(
                state,
                fallback_seen_at=drop.last_seen_at,
            ):
                continue
            fresh.append(drop)
            if len(fresh) >= requested_limit:
                break
        return fresh

    async def reserve_delivery(
        self,
        *,
        guild_id: int,
        drop_id: str,
        channel_id: int,
    ) -> bool:
        await self.ensure_schema()
        guild_text = snowflake_text(guild_id)
        channel_text = snowflake_text(channel_id)
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT state, reserved_at
            FROM movie_ticket_deliveries_v2
            WHERE guild_id = ? AND drop_id = ?
            """,
            (guild_text, drop_id),
        )
        row = await cursor.fetchone()
        now = datetime.now(UTC)
        if row and _delivery_blocks_reservation(row, now=now):
            return False

        # Preserve dedupe for alerts sent before the exact-text migration without
        # reading/guessing legacy snowflake values back into Python.
        if row is None:
            legacy = await conn.execute(
                """
                SELECT state, reserved_at
                FROM movie_ticket_deliveries
                WHERE guild_id = ? AND drop_id = ?
                """,
                (int(guild_id), drop_id),
            )
            legacy_row = await legacy.fetchone()
            if legacy_row and _delivery_blocks_reservation(legacy_row, now=now):
                return False

        now_text = now.isoformat()
        await conn.execute(
            """
            INSERT INTO movie_ticket_deliveries_v2 (
                guild_id, drop_id, channel_id, state, reserved_at, posted_at, message_id, last_error
            ) VALUES (?, ?, ?, 'reserved', ?, NULL, NULL, '')
            ON CONFLICT(guild_id, drop_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                state = 'reserved',
                reserved_at = excluded.reserved_at,
                posted_at = NULL,
                message_id = NULL,
                last_error = ''
            """,
            (guild_text, drop_id, channel_text, now_text),
        )
        await conn.commit()
        return True

    async def mark_delivery_sent(
        self,
        *,
        guild_id: int,
        drop_id: str,
        channel_id: int,
        message_id: int | None,
    ) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE movie_ticket_deliveries_v2
            SET state = 'sent', channel_id = ?, posted_at = ?, message_id = ?, last_error = ''
            WHERE guild_id = ? AND drop_id = ?
            """,
            (
                snowflake_text(channel_id),
                utc_now_iso(),
                optional_snowflake_text(message_id),
                snowflake_text(guild_id),
                drop_id,
            ),
        )
        await conn.commit()

    async def mark_delivery_failed(
        self,
        *,
        guild_id: int,
        drop_id: str,
        error: str,
    ) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE movie_ticket_deliveries_v2
            SET state = 'failed', last_error = ?
            WHERE guild_id = ? AND drop_id = ?
            """,
            (" ".join(str(error or "").split())[:1000], snowflake_text(guild_id), drop_id),
        )
        await conn.commit()

    async def count_sent_for_guild(self, guild_id: int) -> int:
        """Preserve historical count while new exact-text rows take ownership."""

        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
                SELECT drop_id
                FROM movie_ticket_deliveries_v2
                WHERE guild_id = ? AND state = 'sent'
                UNION
                SELECT drop_id
                FROM movie_ticket_deliveries
                WHERE guild_id = ? AND state = 'sent'
            )
            """,
            (snowflake_text(guild_id), int(guild_id)),
        )
        row = await cursor.fetchone()
        return int(_row_value(row, "count") or 0) if row else 0


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


def _delivery_blocks_reservation(row: Any, *, now: datetime) -> bool:
    state = str(_row_value(row, "state") or "")
    reserved_at = _parse_iso(str(_row_value(row, "reserved_at") or ""))
    if state == "sent":
        return True
    return bool(
        state == "reserved"
        and reserved_at is not None
        and now - reserved_at < DELIVERY_RESERVATION_TTL
    )


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
