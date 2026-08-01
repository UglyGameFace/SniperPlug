from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL, clean_text, utc_now_iso


WORKED_RESULT = "worked"
FAILED_RESULT = "failed"
VALID_RESULTS = frozenset({WORKED_RESULT, FAILED_RESULT})


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
    """Persist one redemption result per Discord user and movie-ticket drop."""

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
            conn = self.db.require_conn()
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS movie_ticket_feedback (
                    guild_id INTEGER NOT NULL,
                    drop_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, drop_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_movie_ticket_feedback_counts
                    ON movie_ticket_feedback(guild_id, drop_id, result);
                CREATE INDEX IF NOT EXISTS idx_movie_ticket_delivery_message
                    ON movie_ticket_deliveries(guild_id, message_id);
                """
            )
            await conn.commit()
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
        async with self._vote_lock:
            conn = self.db.require_conn()
            cursor = await conn.execute(
                """
                SELECT result
                FROM movie_ticket_feedback
                WHERE guild_id = ? AND drop_id = ? AND user_id = ?
                """,
                (int(guild_id), drop_id, int(user_id)),
            )
            row = await cursor.fetchone()
            previous = str(_row_value(row, "result") or "") if row else ""
            now = utc_now_iso()
            await conn.execute(
                """
                INSERT INTO movie_ticket_feedback (
                    guild_id, drop_id, user_id, result, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, drop_id, user_id) DO UPDATE SET
                    result = excluded.result,
                    updated_at = excluded.updated_at
                """,
                (int(guild_id), drop_id, int(user_id), normalized, now, now),
            )
            await conn.commit()
            counts = await self._get_counts_unlocked(int(guild_id), drop_id)
            return MovieTicketFeedbackResult(
                previous_result=previous,
                current_result=normalized,
                counts=counts,
            )

    async def get_counts(self, *, guild_id: int, drop_id: str) -> MovieTicketFeedbackCounts:
        await self.ensure_schema()
        return await self._get_counts_unlocked(int(guild_id), drop_id)

    async def _get_counts_unlocked(self, guild_id: int, drop_id: str) -> MovieTicketFeedbackCounts:
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT result, COUNT(*) AS count
            FROM movie_ticket_feedback
            WHERE guild_id = ? AND drop_id = ?
            GROUP BY result
            """,
            (int(guild_id), drop_id),
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
        row = await cursor.fetchone()
        return _context_from_row(row) if row else None

    async def list_recent_deliveries(self, *, limit: int = 100) -> list[MovieTicketFeedbackContext]:
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
            FROM movie_ticket_deliveries AS delivery
            JOIN movie_ticket_drops AS drop_row
              ON drop_row.drop_id = delivery.drop_id
            WHERE delivery.state = 'sent'
              AND delivery.message_id IS NOT NULL
              AND drop_row.active = 1
            ORDER BY delivery.posted_at DESC
            LIMIT ?
            """,
            (max(1, min(250, int(limit))),),
        )
        rows = await cursor.fetchall()
        return [_context_from_row(row) for row in rows]


def _context_from_row(row: Any) -> MovieTicketFeedbackContext:
    return MovieTicketFeedbackContext(
        guild_id=int(_row_value(row, "guild_id") or 0),
        drop_id=str(_row_value(row, "drop_id") or ""),
        channel_id=int(_row_value(row, "channel_id") or 0),
        message_id=int(_row_value(row, "message_id") or 0),
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
