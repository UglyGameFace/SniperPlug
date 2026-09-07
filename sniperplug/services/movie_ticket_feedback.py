from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL, clean_text, utc_now_iso
from sniperplug.services.movie_ticket_snowflake_store import (
    ensure_movie_ticket_snowflake_tables,
    snowflake_int,
    snowflake_text,
)


WORKED_RESULT = "worked"
FAILED_RESULT = "failed"
VALID_RESULTS = frozenset({WORKED_RESULT, FAILED_RESULT})
_SAFE_LEGACY_INTEGER_MAX = (1 << 53) - 1


@dataclass(frozen=True, slots=True)
class MovieTicketFeedbackCounts:
    worked: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.worked + self.failed


@dataclass(frozen=True, slots=True)
class MovieTicketFeedbackContext:
    guild_id: int
    drop_id: str
    channel_id: int
    message_id: int
    offer_url: str
    title: str
    active: bool


@dataclass(frozen=True, slots=True)
class MovieTicketFeedbackResult:
    previous_result: str
    current_result: str
    counts: MovieTicketFeedbackCounts

    @property
    def changed(self) -> bool:
        return bool(self.previous_result and self.previous_result != self.current_result)

    @property
    def repeated(self) -> bool:
        return self.previous_result == self.current_result


class MovieTicketFeedbackStore:
    """Persist one redemption result per user with exact Discord snowflakes."""

    def __init__(self, db: Any):
        self.db = db
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()
        self._vote_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await ensure_movie_ticket_snowflake_tables(self.db)
            self._schema_ready = True

    async def record_vote(
        self,
        *,
        guild_id: int,
        drop_id: str,
        user_id: int,
        result: str,
    ) -> MovieTicketFeedbackResult:
        normalized = clean_text(result).lower()
        if normalized not in VALID_RESULTS:
            raise ValueError(f"Unsupported movie-ticket feedback result: {result!r}")
        if not clean_text(drop_id):
            raise ValueError("drop_id is required")

        await self.ensure_schema()
        guild_text = snowflake_text(guild_id)
        user_text = snowflake_text(user_id)
        async with self._vote_lock:
            conn = self.db.require_conn()
            cursor = await conn.execute(
                """
                SELECT result
                FROM movie_ticket_feedback_v2
                WHERE guild_id = ? AND drop_id = ? AND user_id = ?
                """,
                (guild_text, drop_id, user_text),
            )
            row = await cursor.fetchone()
            previous = str(_row_value(row, "result") or "") if row else ""
            now = utc_now_iso()
            await conn.execute(
                """
                INSERT INTO movie_ticket_feedback_v2 (
                    guild_id, drop_id, user_id, result, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, drop_id, user_id) DO UPDATE SET
                    result = excluded.result,
                    updated_at = excluded.updated_at
                """,
                (guild_text, drop_id, user_text, normalized, now, now),
            )
            await conn.commit()
            counts = await self._get_counts_unlocked(guild_text, drop_id)
            return MovieTicketFeedbackResult(
                previous_result=previous,
                current_result=normalized,
                counts=counts,
            )

    async def get_counts(self, *, guild_id: int, drop_id: str) -> MovieTicketFeedbackCounts:
        await self.ensure_schema()
        return await self._get_counts_unlocked(snowflake_text(guild_id), drop_id)

    async def _get_counts_unlocked(
        self,
        guild_text: str,
        drop_id: str,
    ) -> MovieTicketFeedbackCounts:
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT result, COUNT(*) AS count
            FROM movie_ticket_feedback_v2
            WHERE guild_id = ? AND drop_id = ?
            GROUP BY result
            """,
            (guild_text, drop_id),
        )
        rows = await cursor.fetchall()
        totals = {WORKED_RESULT: 0, FAILED_RESULT: 0}
        for row in rows:
            result = str(_row_value(row, "result") or "")
            if result in totals:
                totals[result] = int(_row_value(row, "count") or 0)
        return MovieTicketFeedbackCounts(
            worked=totals[WORKED_RESULT],
            failed=totals[FAILED_RESULT],
        )

    async def resolve_message(
        self,
        *,
        guild_id: int,
        message_id: int,
    ) -> MovieTicketFeedbackContext | None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT delivery.guild_id,
                   delivery.drop_id,
                   delivery.channel_id,
                   delivery.message_id,
                   drop_row.offer_url,
                   drop_row.title,
                   drop_row.active
            FROM movie_ticket_deliveries_v2 AS delivery
            JOIN movie_ticket_drops AS drop_row
              ON drop_row.drop_id = delivery.drop_id
            WHERE delivery.guild_id = ?
              AND delivery.message_id = ?
              AND delivery.state = 'sent'
            LIMIT 1
            """,
            (snowflake_text(guild_id), snowflake_text(message_id)),
        )
        row = await cursor.fetchone()
        if row:
            return _context_from_row(row)

        # Tiny legacy fixture/test IDs are exact even through numeric result
        # coercion. Real Discord snowflakes are above this boundary, so never
        # guess their old INTEGER values after the exact-text migration.
        if int(guild_id) <= _SAFE_LEGACY_INTEGER_MAX and int(message_id) <= _SAFE_LEGACY_INTEGER_MAX:
            legacy = await conn.execute(
                """
                SELECT delivery.guild_id,
                       delivery.drop_id,
                       delivery.channel_id,
                       delivery.message_id,
                       drop_row.offer_url,
                       drop_row.title,
                       drop_row.active
                FROM movie_ticket_deliveries AS delivery
                JOIN movie_ticket_drops AS drop_row
                  ON drop_row.drop_id = delivery.drop_id
                WHERE delivery.guild_id = ?
                  AND delivery.message_id = ?
                  AND delivery.state = 'sent'
                LIMIT 1
                """,
                (int(guild_id), int(message_id)),
            )
            legacy_row = await legacy.fetchone()
            return _legacy_context_from_row(legacy_row) if legacy_row else None
        return None

    async def list_recent_deliveries(self, *, limit: int = 100) -> list[MovieTicketFeedbackContext]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        safe_limit = max(1, min(250, int(limit)))
        cursor = await conn.execute(
            """
            SELECT delivery.guild_id,
                   delivery.drop_id,
                   delivery.channel_id,
                   delivery.message_id,
                   drop_row.offer_url,
                   drop_row.title,
                   drop_row.active
            FROM movie_ticket_deliveries_v2 AS delivery
            JOIN movie_ticket_drops AS drop_row
              ON drop_row.drop_id = delivery.drop_id
            WHERE delivery.state = 'sent'
              AND delivery.message_id IS NOT NULL
              AND drop_row.active = 1
            ORDER BY delivery.posted_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        rows = await cursor.fetchall()
        contexts = [_context_from_row(row) for row in rows]
        if len(contexts) >= safe_limit:
            return contexts[:safe_limit]

        # Compatibility only for pre-migration tests/fixtures with small exact
        # integers. Unsafe real legacy snowflakes are deliberately ignored.
        legacy = await conn.execute(
            """
            SELECT delivery.guild_id,
                   delivery.drop_id,
                   delivery.channel_id,
                   delivery.message_id,
                   drop_row.offer_url,
                   drop_row.title,
                   drop_row.active
            FROM movie_ticket_deliveries AS delivery
            JOIN movie_ticket_drops AS drop_row
              ON drop_row.drop_id = delivery.drop_id
            WHERE delivery.state = 'sent'
              AND delivery.message_id IS NOT NULL
              AND drop_row.active = 1
            ORDER BY delivery.posted_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        for row in await legacy.fetchall():
            context = _legacy_context_from_row(row)
            if context is None:
                continue
            if any(
                existing.guild_id == context.guild_id
                and existing.message_id == context.message_id
                for existing in contexts
            ):
                continue
            contexts.append(context)
            if len(contexts) >= safe_limit:
                break
        return contexts


def _context_from_row(row: Any) -> MovieTicketFeedbackContext:
    return MovieTicketFeedbackContext(
        guild_id=snowflake_int(_row_value(row, "guild_id")),
        drop_id=str(_row_value(row, "drop_id") or ""),
        channel_id=snowflake_int(_row_value(row, "channel_id")),
        message_id=snowflake_int(_row_value(row, "message_id")),
        offer_url=str(_row_value(row, "offer_url") or ATOM_PROMOTIONS_URL),
        title=str(_row_value(row, "title") or "Movie ticket drop"),
        active=bool(_row_value(row, "active")),
    )


def _legacy_context_from_row(row: Any) -> MovieTicketFeedbackContext | None:
    if row is None:
        return None
    try:
        guild_id = int(_row_value(row, "guild_id") or 0)
        channel_id = int(_row_value(row, "channel_id") or 0)
        message_id = int(_row_value(row, "message_id") or 0)
    except (TypeError, ValueError):
        return None
    if (
        guild_id <= 0
        or channel_id <= 0
        or message_id <= 0
        or max(guild_id, channel_id, message_id) > _SAFE_LEGACY_INTEGER_MAX
    ):
        return None
    return MovieTicketFeedbackContext(
        guild_id=guild_id,
        drop_id=str(_row_value(row, "drop_id") or ""),
        channel_id=channel_id,
        message_id=message_id,
        offer_url=str(_row_value(row, "offer_url") or ATOM_PROMOTIONS_URL),
        title=str(_row_value(row, "title") or "Movie ticket drop"),
        active=bool(_row_value(row, "active")),
    )


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
