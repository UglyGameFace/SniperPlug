from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import discord

from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL, MovieTicketDrop, clean_text


log = logging.getLogger("sniperplug.movie_ticket_feedback")

WORKED_VERDICT = "worked"
FAILED_VERDICT = "failed"
VALID_VERDICTS = frozenset({WORKED_VERDICT, FAILED_VERDICT})
RECENT_FEEDBACK_VIEW_LIMIT = 500
RECENT_DELIVERY_UPGRADE_LIMIT = 150


@dataclass(frozen=True, slots=True)
class MovieTicketFeedbackCounts:
    worked: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.worked + self.failed

    @property
    def summary(self) -> str:
        if self.total == 0:
            return "No community reports yet."
        return f"**{self.worked} worked** • **{self.failed} didn’t work**"


@dataclass(frozen=True, slots=True)
class MovieTicketFeedbackResult:
    applied: bool
    duplicate: bool
    changed_vote: bool
    verdict: str
    counts: MovieTicketFeedbackCounts


@dataclass(frozen=True, slots=True)
class MovieTicketFeedbackTarget:
    drop_id: str
    offer_url: str


@dataclass(frozen=True, slots=True)
class MovieTicketDeliveryTarget:
    guild_id: int
    channel_id: int
    message_id: int
    drop_id: str
    offer_url: str


async def ensure_movie_ticket_feedback_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS movie_ticket_feedback_votes (
            drop_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            guild_id INTEGER,
            verdict TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (drop_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_movie_ticket_feedback_drop
            ON movie_ticket_feedback_votes(drop_id, verdict);
        CREATE INDEX IF NOT EXISTS idx_movie_ticket_feedback_updated
            ON movie_ticket_feedback_votes(updated_at DESC);
        """
    )
    await conn.commit()


async def get_movie_ticket_feedback_counts(db: Any, drop_id: str) -> MovieTicketFeedbackCounts:
    await ensure_movie_ticket_feedback_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT
            SUM(CASE WHEN verdict = 'worked' THEN 1 ELSE 0 END) AS worked_count,
            SUM(CASE WHEN verdict = 'failed' THEN 1 ELSE 0 END) AS failed_count
        FROM movie_ticket_feedback_votes
        WHERE drop_id = ?
        """,
        (clean_text(drop_id),),
    )
    row = await cursor.fetchone()
    return MovieTicketFeedbackCounts(
        worked=int(_row_value(row, "worked_count") or 0),
        failed=int(_row_value(row, "failed_count") or 0),
    )


async def record_movie_ticket_feedback(
    db: Any,
    *,
    drop_id: str,
    user_id: int,
    guild_id: int | None,
    verdict: str,
) -> MovieTicketFeedbackResult:
    normalized_drop_id = clean_text(drop_id)
    normalized_verdict = clean_text(verdict).lower()
    if normalized_verdict not in VALID_VERDICTS or not normalized_drop_id:
        return MovieTicketFeedbackResult(
            applied=False,
            duplicate=False,
            changed_vote=False,
            verdict=normalized_verdict,
            counts=MovieTicketFeedbackCounts(),
        )

    await ensure_movie_ticket_feedback_table(db)
    conn = db.require_conn()

    drop_cursor = await conn.execute(
        "SELECT 1 AS found FROM movie_ticket_drops WHERE drop_id = ? LIMIT 1",
        (normalized_drop_id,),
    )
    if not await drop_cursor.fetchone():
        return MovieTicketFeedbackResult(
            applied=False,
            duplicate=False,
            changed_vote=False,
            verdict=normalized_verdict,
            counts=MovieTicketFeedbackCounts(),
        )

    cursor = await conn.execute(
        "SELECT verdict FROM movie_ticket_feedback_votes WHERE drop_id = ? AND user_id = ?",
        (normalized_drop_id, int(user_id)),
    )
    row = await cursor.fetchone()
    previous = clean_text(_row_value(row, "verdict")).lower() if row else ""
    if previous == normalized_verdict:
        counts = await get_movie_ticket_feedback_counts(db, normalized_drop_id)
        return MovieTicketFeedbackResult(
            applied=False,
            duplicate=True,
            changed_vote=False,
            verdict=normalized_verdict,
            counts=counts,
        )

    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO movie_ticket_feedback_votes (drop_id, user_id, guild_id, verdict, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(drop_id, user_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            verdict = excluded.verdict,
            updated_at = excluded.updated_at
        """,
        (
            normalized_drop_id,
            int(user_id),
            int(guild_id) if guild_id else None,
            normalized_verdict,
            now,
        ),
    )
    await conn.commit()
    counts = await get_movie_ticket_feedback_counts(db, normalized_drop_id)
    return MovieTicketFeedbackResult(
        applied=True,
        duplicate=False,
        changed_vote=bool(previous and previous != normalized_verdict),
        verdict=normalized_verdict,
        counts=counts,
    )


async def list_recent_movie_ticket_feedback_targets(
    db: Any,
    *,
    limit: int = RECENT_FEEDBACK_VIEW_LIMIT,
) -> list[MovieTicketFeedbackTarget]:
    await ensure_movie_ticket_feedback_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT drop_id, offer_url
        FROM movie_ticket_drops
        ORDER BY active DESC, last_seen_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 2000)),),
    )
    rows = await cursor.fetchall()
    return [
        MovieTicketFeedbackTarget(
            drop_id=clean_text(_row_value(row, "drop_id")),
            offer_url=clean_text(_row_value(row, "offer_url")) or ATOM_PROMOTIONS_URL,
        )
        for row in rows
        if clean_text(_row_value(row, "drop_id"))
    ]


async def list_recent_movie_ticket_deliveries(
    db: Any,
    *,
    limit: int = RECENT_DELIVERY_UPGRADE_LIMIT,
) -> list[MovieTicketDeliveryTarget]:
    await ensure_movie_ticket_feedback_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT d.guild_id, d.channel_id, d.message_id, d.drop_id, p.offer_url
        FROM movie_ticket_deliveries AS d
        JOIN movie_ticket_drops AS p ON p.drop_id = d.drop_id
        WHERE d.state = 'sent' AND d.message_id IS NOT NULL
        ORDER BY d.posted_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 1000)),),
    )
    rows = await cursor.fetchall()
    targets: list[MovieTicketDeliveryTarget] = []
    for row in rows:
        message_id = _optional_int(_row_value(row, "message_id"))
        channel_id = _optional_int(_row_value(row, "channel_id"))
        guild_id = _optional_int(_row_value(row, "guild_id"))
        drop_id = clean_text(_row_value(row, "drop_id"))
        if not message_id or not channel_id or not guild_id or not drop_id:
            continue
        targets.append(
            MovieTicketDeliveryTarget(
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=message_id,
                drop_id=drop_id,
                offer_url=clean_text(_row_value(row, "offer_url")) or ATOM_PROMOTIONS_URL,
            )
        )
    return targets


async def build_movie_ticket_feedback_view(
    bot: Any,
    drop: MovieTicketDrop,
    *,
    demo: bool = False,
) -> "MovieTicketFeedbackView":
    counts = MovieTicketFeedbackCounts()
    if not demo:
        counts = await get_movie_ticket_feedback_counts(bot.db, drop.drop_id)
    return MovieTicketFeedbackView(
        bot,
        drop_id=drop.drop_id,
        offer_url=drop.offer_url,
        counts=counts,
        demo=demo,
    )


async def register_persistent_movie_ticket_feedback_views(bot: Any) -> int:
    db = getattr(bot, "db", None)
    if db is None:
        return 0
    targets = await list_recent_movie_ticket_feedback_targets(db)
    registered = 0
    for target in targets:
        try:
            counts = await get_movie_ticket_feedback_counts(db, target.drop_id)
            bot.add_view(
                MovieTicketFeedbackView(
                    bot,
                    drop_id=target.drop_id,
                    offer_url=target.offer_url,
                    counts=counts,
                )
            )
            registered += 1
        except Exception:
            log.exception("Could not register movie feedback view drop=%s", target.drop_id)
    return registered


class MovieTicketFeedbackView(discord.ui.View):
    def __init__(
        self,
        bot: Any,
        *,
        drop_id: str,
        offer_url: str,
        counts: MovieTicketFeedbackCounts | None = None,
        demo: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.drop_id = clean_text(drop_id)
        self.offer_url = clean_text(offer_url) or ATOM_PROMOTIONS_URL
        self.counts = counts or MovieTicketFeedbackCounts()
        self.demo = bool(demo)

        self.add_item(
            discord.ui.Button(
                label="Open official offer",
                emoji="🔗",
                style=discord.ButtonStyle.link,
                url=self.offer_url,
            )
        )
        self.add_item(
            MovieTicketFeedbackButton(
                verdict=WORKED_VERDICT,
                drop_id=self.drop_id,
                count=self.counts.worked,
                disabled=self.demo,
            )
        )
        self.add_item(
            MovieTicketFeedbackButton(
                verdict=FAILED_VERDICT,
                drop_id=self.drop_id,
                count=self.counts.failed,
                disabled=self.demo,
            )
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        log.error(
            "Movie ticket feedback component failed item=%s drop=%s user=%s guild=%s",
            getattr(item, "custom_id", type(item).__name__),
            self.drop_id,
            getattr(interaction.user, "id", None),
            interaction.guild_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await safe_movie_feedback_reply(
            interaction,
            "Your report could not be saved just now. Nothing was counted; please try again.",
        )


class MovieTicketFeedbackButton(discord.ui.Button):
    def __init__(self, *, verdict: str, drop_id: str, count: int, disabled: bool = False) -> None:
        if verdict == WORKED_VERDICT:
            label = f"Worked · {max(0, int(count))}"
            emoji = "✅"
            style = discord.ButtonStyle.success
        else:
            label = f"Didn’t Work · {max(0, int(count))}"
            emoji = "❌"
            style = discord.ButtonStyle.danger
        custom_id = f"movie_ticket_feedback:{verdict}:{clean_text(drop_id)}"
        super().__init__(
            label=label[:80],
            emoji=emoji,
            style=style,
            custom_id=custom_id[:100],
            disabled=disabled,
        )
        self.verdict = verdict
        self.drop_id = clean_text(drop_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        if interaction.guild_id is None or interaction.user is None:
            await safe_movie_feedback_reply(interaction, "Use these feedback buttons inside the server where the alert was posted.")
            return

        db = getattr(interaction.client, "db", None)
        if db is None:
            await safe_movie_feedback_reply(interaction, "The feedback database is unavailable right now. Nothing was counted.")
            return

        result = await record_movie_ticket_feedback(
            db,
            drop_id=self.drop_id,
            user_id=int(interaction.user.id),
            guild_id=int(interaction.guild_id),
            verdict=self.verdict,
        )
        if not result.applied and not result.duplicate:
            await safe_movie_feedback_reply(interaction, "This ticket drop is no longer recognized, so no vote was counted.")
            return

        view = self.view if isinstance(self.view, MovieTicketFeedbackView) else None
        offer_url = view.offer_url if view else ATOM_PROMOTIONS_URL
        updated_view = MovieTicketFeedbackView(
            interaction.client,
            drop_id=self.drop_id,
            offer_url=offer_url,
            counts=result.counts,
        )
        message = interaction.message
        if message is not None:
            try:
                await message.edit(view=updated_view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.warning("Could not refresh movie feedback button counts drop=%s message=%s", self.drop_id, message.id)

        verdict_label = "Worked" if result.verdict == WORKED_VERDICT else "Didn’t Work"
        if result.duplicate:
            prefix = f"You already marked this as **{verdict_label}**. Duplicate vote ignored."
        elif result.changed_vote:
            prefix = f"Updated your report to **{verdict_label}**."
        else:
            prefix = f"Saved: **{verdict_label}**. Thanks—this helps everyone know whether the code is still redeeming."
        await safe_movie_feedback_reply(interaction, f"{prefix}\n{result.counts.summary}")


async def safe_movie_feedback_reply(interaction: discord.Interaction, message: str) -> None:
    text = clean_text(message)[:1900]
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.NotFound:
        return
    except Exception:
        log.exception("Movie feedback response failed user=%s guild=%s", getattr(interaction.user, "id", None), interaction.guild_id)


def _row_value(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
