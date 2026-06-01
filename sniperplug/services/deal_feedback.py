from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from sniperplug.services.public_posting import normalize_retailer_key


POPULAR_BRAND_TERMS = {
    "apple", "samsung", "lg", "sony", "dell", "hp", "lenovo", "asus", "acer", "msi", "nvidia", "amd", "intel",
    "logitech", "razer", "hyperx", "corsair", "steelseries", "sandisk", "western digital", "wd", "seagate", "crucial", "kingston", "anker", "belkin",
    "jbl", "bose", "beats", "roku", "onn", "tcl", "hisense", "vizio", "shark", "dyson", "bissell", "hoover", "blackstone", "ninja", "keurig", "instant pot", "kitchenaid",
    "milwaukee", "dewalt", "hart", "ryobi", "craftsman", "stanley", "husky", "kobalt", "lego", "pokemon", "barbie", "hot wheels", "nerf", "hasbro", "mattel", "nintendo", "xbox", "playstation",
    "nike", "adidas", "puma", "reebok", "under armour", "levi", "calvin klein", "tommy hilfiger", "dolce", "gabbana", "versace", "gucci", "ysl", "armani", "dior", "burberry", "coach", "polo", "ralph lauren",
    "cerave", "cetaphil", "neutrogena", "olay", "dove", "gillette", "tide", "gain", "persil", "bounty", "charmin", "scott", "huggies", "pampers",
}


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


@dataclass(frozen=True)
class FeedbackAdjustment:
    target_key: str
    brand_hint: str
    product_score: int = 0
    brand_score: int = 0

    @property
    def total(self) -> int:
        return max(-60, min(60, self.product_score + self.brand_score))

    @property
    def summary(self) -> str:
        if self.total > 0:
            return f"feedback boost +{self.total}"
        if self.total < 0:
            return f"feedback penalty {self.total}"
        return "no feedback adjustment"


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


async def apply_feedback_learning_to_cards(db, *, guild_id: int | None, cards: list[Any], fallback_retailer: str = "walmart") -> list[Any]:
    """Sort cards using saved deal/brand feedback.

    Good/Flip feedback boosts future similar cards. Bad/Bad Brand/Too Weak
    lowers future cards. The original card score still matters; feedback only
    nudges the already-ranked list instead of taking over completely.
    """
    if db is None or guild_id is None or not cards:
        return cards
    adjusted: list[tuple[float, int, Any]] = []
    for index, card in enumerate(cards):
        adjustment = await get_feedback_adjustment(db, guild_id=guild_id, card=card, fallback_retailer=fallback_retailer)
        setattr(card, "feedback_learning_score", adjustment.total)
        if adjustment.total:
            annotate_card_with_feedback_learning(card, adjustment)
        base_score = float(getattr(card, "score", 0) or 0)
        discount = float(getattr(card, "discount", 0) or 0)
        adjusted_score = base_score + discount * 0.45 + adjustment.total * 3.0
        adjusted.append((adjusted_score, -index, card))
    adjusted.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [card for _, __, card in adjusted]


async def get_feedback_adjustment(db, *, guild_id: int, card: Any, fallback_retailer: str = "walmart") -> FeedbackAdjustment:
    await ensure_deal_feedback_tables(db)
    retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
    target_key = feedback_product_key(card, retailer=retailer)
    brand_hint = guess_brand_hint(card)
    conn = db.require_conn()
    product_score = 0
    brand_score = 0
    cursor = await conn.execute(
        "SELECT total_score FROM guild_deal_feedback_summary WHERE guild_id = ? AND target_key = ?",
        (guild_id, target_key),
    )
    row = await cursor.fetchone()
    if row and row["total_score"] is not None:
        product_score = max(-40, min(40, int(row["total_score"]) * 5))
    if brand_hint:
        cursor = await conn.execute(
            "SELECT total_score FROM guild_deal_brand_feedback_summary WHERE guild_id = ? AND retailer = ? AND brand_hint = ?",
            (guild_id, retailer, brand_hint),
        )
        row = await cursor.fetchone()
        if row and row["total_score"] is not None:
            brand_score = max(-30, min(30, int(row["total_score"]) * 3))
    return FeedbackAdjustment(target_key=target_key, brand_hint=brand_hint, product_score=product_score, brand_score=brand_score)


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


def feedback_product_key(card: Any, *, retailer: str) -> str:
    identity = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or canonical_url_key(str(getattr(card, "url", "") or ""))
    return ":".join((normalize_retailer_key(retailer), identity or "unknown"))


def canonical_url_key(url: str) -> str:
    return (url or "").strip().split("?", 1)[0].rstrip("/") or "unknown"


def card_text(card: Any) -> str:
    embed = getattr(card, "embed", None)
    pieces: list[str] = [str(getattr(card, "label", "") or "")]
    if embed is not None:
        pieces.append(str(getattr(embed, "title", "") or ""))
        pieces.append(str(getattr(embed, "description", "") or ""))
        for field in getattr(embed, "fields", []) or []:
            pieces.append(str(getattr(field, "name", "") or ""))
            pieces.append(str(getattr(field, "value", "") or ""))
    return " ".join(pieces).lower()


def annotate_card_with_feedback_learning(card: Any, adjustment: FeedbackAdjustment) -> None:
    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return
    if any(str(field.name or "") == "🧠 Feedback learning" for field in embed.fields):
        return
    emoji = "📈" if adjustment.total > 0 else "📉"
    embed.add_field(
        name="🧠 Feedback learning",
        value=f"{emoji} {adjustment.summary}",
        inline=False,
    )
