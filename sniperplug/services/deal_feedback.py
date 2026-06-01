from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from sniperplug.services.deal_search_modes import POPULAR_BRAND_TERMS, card_text
from sniperplug.services.public_posting import normalize_retailer_key


@dataclass(frozen=True)
class DealFeedbackTarget:
    target_key: str
    retailer: str
    title: str
    url: str
    source_label: str
    brand_hint: str = ""


@dataclass(frozen=True)
class FeedbackAction:
    key: str
    label: str
    emoji: str
    column: str
    score_delta: int
    response: str


FEEDBACK_ACTIONS: dict[str, FeedbackAction] = {
    "good": FeedbackAction("good", "Good Deal", "👍", "good_count", 2, "Marked as a good deal. SniperPlug will learn from that."),
    "bad": FeedbackAction("bad", "Bad Pick", "👎", "bad_count", -2, "Marked as a bad pick. SniperPlug will lower similar picks over time."),
    "bad_brand": FeedbackAction("bad_brand", "Bad Brand", "🚫", "bad_brand_count", -3, "Marked as a bad brand/product family signal."),
    "flip": FeedbackAction("flip", "Flip Worthy", "💰", "flip_count", 3, "Marked as flip/resale worthy. SniperPlug will boost similar wins."),
    "weak": FeedbackAction("weak", "Too Weak", "🧊", "weak_count", -1, "Marked as too weak for public hype."),
}


class DealFeedbackView(discord.ui.View):
    """Per-message deal feedback buttons.

    This is normal view code, not a runtime patch. The feedback is saved to DB
    so future ranking can learn from staff/public judgement.
    """

    def __init__(self, target: DealFeedbackTarget):
        super().__init__(timeout=86400)
        self.target = target
        for action in FEEDBACK_ACTIONS.values():
            self.add_item(DealFeedbackButton(action))


class DealFeedbackButton(discord.ui.Button):
    def __init__(self, action: FeedbackAction):
        style = discord.ButtonStyle.success if action.key in {"good", "flip"} else discord.ButtonStyle.secondary
        if action.key in {"bad", "bad_brand"}:
            style = discord.ButtonStyle.danger
        super().__init__(label=action.label, emoji=action.emoji, style=style, custom_id=f"deal_feedback:{action.key}")
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealFeedbackView):
            await interaction.response.send_message("This feedback panel is no longer active.", ephemeral=True)
            return
        db = getattr(interaction.client, "db", None)
        if db is None or interaction.guild_id is None:
            await interaction.response.send_message("Feedback could not be saved because the bot database/server context is unavailable.", ephemeral=True)
            return
        await record_deal_feedback(
            db,
            guild_id=interaction.guild_id,
            target=self.view.target,
            action=self.action.key,
            user_id=getattr(interaction.user, "id", None),
        )
        await interaction.response.send_message(f"{self.action.emoji} {self.action.response}", ephemeral=True)


async def record_deal_feedback(db, *, guild_id: int, target: DealFeedbackTarget, action: str, user_id: int | None) -> None:
    feedback = FEEDBACK_ACTIONS.get(action)
    if feedback is None:
        return
    await ensure_deal_feedback_tables(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    retailer = normalize_retailer_key(target.retailer)
    await conn.execute(
        """
        INSERT INTO guild_deal_feedback_events (
            guild_id, target_key, retailer, title, url, brand_hint, source_label, action, score_delta, user_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            target.target_key,
            retailer,
            target.title[:300],
            target.url[:800],
            target.brand_hint[:120],
            target.source_label[:120],
            feedback.key,
            feedback.score_delta,
            str(user_id) if user_id is not None else None,
            now,
        ),
    )
    await conn.execute(
        """
        INSERT INTO guild_deal_feedback_summary (
            guild_id, target_key, retailer, title, url, brand_hint, good_count, bad_count, bad_brand_count, flip_count, weak_count, total_score, last_action_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, target_key) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            brand_hint = excluded.brand_hint,
            good_count = guild_deal_feedback_summary.good_count + excluded.good_count,
            bad_count = guild_deal_feedback_summary.bad_count + excluded.bad_count,
            bad_brand_count = guild_deal_feedback_summary.bad_brand_count + excluded.bad_brand_count,
            flip_count = guild_deal_feedback_summary.flip_count + excluded.flip_count,
            weak_count = guild_deal_feedback_summary.weak_count + excluded.weak_count,
            total_score = guild_deal_feedback_summary.total_score + excluded.total_score,
            last_action_at = excluded.last_action_at
        """,
        (
            guild_id,
            target.target_key,
            retailer,
            target.title[:300],
            target.url[:800],
            target.brand_hint[:120],
            1 if feedback.column == "good_count" else 0,
            1 if feedback.column == "bad_count" else 0,
            1 if feedback.column == "bad_brand_count" else 0,
            1 if feedback.column == "flip_count" else 0,
            1 if feedback.column == "weak_count" else 0,
            feedback.score_delta,
            now,
        ),
    )
    if target.brand_hint:
        await conn.execute(
            """
            INSERT INTO guild_deal_brand_feedback_summary (
                guild_id, retailer, brand_hint, good_count, bad_count, bad_brand_count, flip_count, weak_count, total_score, last_action_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, retailer, brand_hint) DO UPDATE SET
                good_count = guild_deal_brand_feedback_summary.good_count + excluded.good_count,
                bad_count = guild_deal_brand_feedback_summary.bad_count + excluded.bad_count,
                bad_brand_count = guild_deal_brand_feedback_summary.bad_brand_count + excluded.bad_brand_count,
                flip_count = guild_deal_brand_feedback_summary.flip_count + excluded.flip_count,
                weak_count = guild_deal_brand_feedback_summary.weak_count + excluded.weak_count,
                total_score = guild_deal_brand_feedback_summary.total_score + excluded.total_score,
                last_action_at = excluded.last_action_at
            """,
            (
                guild_id,
                retailer,
                target.brand_hint[:120],
                1 if feedback.column == "good_count" else 0,
                1 if feedback.column == "bad_count" else 0,
                1 if feedback.column == "bad_brand_count" else 0,
                1 if feedback.column == "flip_count" else 0,
                1 if feedback.column == "weak_count" else 0,
                feedback.score_delta,
                now,
            ),
        )
    await conn.commit()


async def ensure_deal_feedback_tables(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_deal_feedback_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            target_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            brand_hint TEXT,
            source_label TEXT NOT NULL,
            action TEXT NOT NULL,
            score_delta INTEGER NOT NULL,
            user_id TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_deal_feedback_summary (
            guild_id INTEGER NOT NULL,
            target_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            brand_hint TEXT,
            good_count INTEGER NOT NULL DEFAULT 0,
            bad_count INTEGER NOT NULL DEFAULT 0,
            bad_brand_count INTEGER NOT NULL DEFAULT 0,
            flip_count INTEGER NOT NULL DEFAULT 0,
            weak_count INTEGER NOT NULL DEFAULT 0,
            total_score INTEGER NOT NULL DEFAULT 0,
            last_action_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, target_key)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_deal_brand_feedback_summary (
            guild_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            brand_hint TEXT NOT NULL,
            good_count INTEGER NOT NULL DEFAULT 0,
            bad_count INTEGER NOT NULL DEFAULT 0,
            bad_brand_count INTEGER NOT NULL DEFAULT 0,
            flip_count INTEGER NOT NULL DEFAULT 0,
            weak_count INTEGER NOT NULL DEFAULT 0,
            total_score INTEGER NOT NULL DEFAULT 0,
            last_action_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, retailer, brand_hint)
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_feedback_events_guild_created ON guild_deal_feedback_events (guild_id, created_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_feedback_summary_guild_score ON guild_deal_feedback_summary (guild_id, total_score)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_brand_feedback_summary_guild_score ON guild_deal_brand_feedback_summary (guild_id, total_score)")
    await conn.commit()


def build_feedback_target(card: Any, *, target_key: str, retailer: str, source_label: str) -> DealFeedbackTarget:
    return DealFeedbackTarget(
        target_key=target_key,
        retailer=retailer,
        title=str(getattr(card, "label", None) or getattr(getattr(card, "embed", None), "title", None) or "deal"),
        url=str(getattr(card, "url", "") or ""),
        source_label=source_label,
        brand_hint=guess_brand_hint(card),
    )


def guess_brand_hint(card: Any) -> str:
    text = card_text(card)
    best = ""
    for brand in sorted(POPULAR_BRAND_TERMS, key=len, reverse=True):
        if brand in text:
            best = brand
            break
    return best[:120]
