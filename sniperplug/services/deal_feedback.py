from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

FEEDBACK_TARGET_DAYS = 45


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


@dataclass(frozen=True)
class FeedbackRecordResult:
    applied: bool
    duplicate: bool
    changed_vote: bool
    message: str


FEEDBACK_ACTIONS: dict[str, FeedbackAction] = {
    "good": FeedbackAction("good", "Good Deal", "👍", "good_count", 2, "Marked as a good deal. SniperPlug will learn from that."),
    "bad": FeedbackAction("bad", "Bad Pick", "👎", "bad_count", -2, "Marked as a bad pick. SniperPlug will lower similar picks over time."),
    "bad_brand": FeedbackAction("bad_brand", "Bad Brand", "🚫", "bad_brand_count", -3, "Marked as a bad brand/product family signal."),
    "flip": FeedbackAction("flip", "Flip Worthy", "💰", "flip_count", 3, "Marked as flip/resale worthy. SniperPlug will boost similar wins."),
    "weak": FeedbackAction("weak", "Too Weak", "🧊", "weak_count", -1, "Marked as too weak for public hype."),
}


class DealFeedbackView(discord.ui.View):
    """Per-message feedback buttons with persistent token support."""

    def __init__(self, target: DealFeedbackTarget | None = None, *, token: str | None = None, persistent: bool = False):
        super().__init__(timeout=None if persistent else 86400)
        self.target = target
        self.token = token or ""
        for action in FEEDBACK_ACTIONS.values():
            self.add_item(DealFeedbackButton(action, token=self.token if persistent else None))


class DealFeedbackButton(discord.ui.Button):
    def __init__(self, action: FeedbackAction, *, token: str | None = None):
        style = discord.ButtonStyle.success if action.key in {"good", "flip"} else discord.ButtonStyle.secondary
        if action.key in {"bad", "bad_brand"}:
            style = discord.ButtonStyle.danger
        custom_id = f"deal_feedback:{action.key}:{token}" if token else f"deal_feedback:{action.key}"
        super().__init__(label=action.label, emoji=action.emoji, style=style, custom_id=custom_id[:100])
        self.action = action
        self.token = token or ""

    async def callback(self, interaction: discord.Interaction) -> None:
        db = getattr(interaction.client, "db", None)
        if db is None or interaction.guild_id is None:
            await interaction.response.send_message("Feedback could not be saved because the bot database/server context is unavailable.", ephemeral=True)
            return
        target = getattr(self.view, "target", None) if isinstance(self.view, DealFeedbackView) else None
        if target is None and self.token:
            target = await get_feedback_target(db, token=self.token, guild_id=interaction.guild_id)
        if target is None:
            await interaction.response.send_message("This feedback target is no longer available. Newer deal posts will keep working across restarts.", ephemeral=True)
            return
        result = await record_deal_feedback(
            db,
            guild_id=interaction.guild_id,
            target=target,
            action=self.action.key,
            user_id=getattr(interaction.user, "id", None),
        )
        await interaction.response.send_message(result.message, ephemeral=True)


async def build_deal_feedback_view(db, *, guild_id: int, target: DealFeedbackTarget) -> DealFeedbackView:
    token = await save_feedback_target(db, guild_id=guild_id, target=target)
    return DealFeedbackView(target, token=token, persistent=True)


async def register_persistent_feedback_views(bot: Any) -> int:
    db = getattr(bot, "db", None)
    if db is None:
        return 0
    targets = await recent_feedback_targets(db)
    registered = 0
    for token, target in targets:
        try:
            bot.add_view(DealFeedbackView(target, token=token, persistent=True))
            registered += 1
        except Exception:
            continue
    return registered


async def save_feedback_target(db, *, guild_id: int, target: DealFeedbackTarget) -> str:
    await ensure_deal_feedback_tables(db)
    conn = db.require_conn()
    token = feedback_token(guild_id=guild_id, target=target)
    now = datetime.now(timezone.utc).isoformat()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=FEEDBACK_TARGET_DAYS)).isoformat()
    await conn.execute(
        """
        INSERT INTO guild_deal_feedback_targets (
            token, guild_id, target_key, retailer, title, url, brand_hint, source_label, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            brand_hint = excluded.brand_hint,
            source_label = excluded.source_label,
            expires_at = excluded.expires_at
        """,
        (
            token,
            guild_id,
            target.target_key,
            normalize_retailer_key(target.retailer),
            target.title[:300],
            target.url[:800],
            target.brand_hint[:120],
            target.source_label[:120],
            now,
            expires_at,
        ),
    )
    await conn.commit()
    return token


async def get_feedback_target(db, *, token: str, guild_id: int | None = None) -> DealFeedbackTarget | None:
    await ensure_deal_feedback_tables(db)
    conn = db.require_conn()
    query = "SELECT * FROM guild_deal_feedback_targets WHERE token = ? AND expires_at > ?"
    params: tuple[Any, ...] = (token, datetime.now(timezone.utc).isoformat())
    if guild_id is not None:
        query += " AND guild_id = ?"
        params = (*params, guild_id)
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    return row_to_feedback_target(row) if row else None


async def recent_feedback_targets(db, *, limit: int = 750) -> list[tuple[str, DealFeedbackTarget]]:
    await ensure_deal_feedback_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT * FROM guild_deal_feedback_targets
        WHERE expires_at > ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (datetime.now(timezone.utc).isoformat(), max(1, min(int(limit), 2000))),
    )
    rows = await cursor.fetchall()
    return [(str(row["token"]), row_to_feedback_target(row)) for row in rows]


def row_to_feedback_target(row: Any) -> DealFeedbackTarget:
    return DealFeedbackTarget(
        target_key=str(row["target_key"]),
        retailer=str(row["retailer"]),
        title=str(row["title"]),
        url=str(row["url"]),
        source_label=str(row["source_label"]),
        brand_hint=str(row["brand_hint"] or ""),
    )


def feedback_token(*, guild_id: int, target: DealFeedbackTarget) -> str:
    raw = f"{guild_id}|{normalize_retailer_key(target.retailer)}|{target.target_key}|{target.source_label}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:22]


async def record_deal_feedback(db, *, guild_id: int, target: DealFeedbackTarget, action: str, user_id: int | None) -> FeedbackRecordResult:
    feedback = FEEDBACK_ACTIONS.get(action)
    if feedback is None:
        return FeedbackRecordResult(False, False, False, "That feedback action is not valid anymore.")

    await ensure_deal_feedback_tables(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    retailer = normalize_retailer_key(target.retailer)
    user_key = str(user_id) if user_id is not None else "anonymous"

    existing_action: str | None = None
    if user_id is not None:
        cursor = await conn.execute(
            "SELECT action FROM guild_deal_feedback_user_votes WHERE guild_id = ? AND target_key = ? AND user_id = ?",
            (guild_id, target.target_key, user_key),
        )
        row = await cursor.fetchone()
        existing_action = str(row["action"]) if row and row["action"] else None

    if existing_action == feedback.key:
        await insert_feedback_event(conn, guild_id=guild_id, target=target, action=feedback.key, retailer=retailer, score_delta=0, user_key=user_key, created_at=now)
        await conn.commit()
        return FeedbackRecordResult(False, True, False, f"{feedback.emoji} You already marked this as **{feedback.label}**. Duplicate vote ignored so learning stays clean.")

    changed_vote = False
    if existing_action and existing_action in FEEDBACK_ACTIONS:
        old_feedback = FEEDBACK_ACTIONS[existing_action]
        await apply_feedback_delta(conn, guild_id=guild_id, target=target, feedback=old_feedback, retailer=retailer, sign=-1, updated_at=now)
        changed_vote = True

    await insert_feedback_event(conn, guild_id=guild_id, target=target, action=feedback.key, retailer=retailer, score_delta=feedback.score_delta, user_key=user_key, created_at=now)
    await apply_feedback_delta(conn, guild_id=guild_id, target=target, feedback=feedback, retailer=retailer, sign=1, updated_at=now)

    if user_id is not None:
        await conn.execute(
            """
            INSERT INTO guild_deal_feedback_user_votes (
                guild_id, target_key, user_id, action, retailer, brand_hint, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, target_key, user_id) DO UPDATE SET
                action = excluded.action,
                retailer = excluded.retailer,
                brand_hint = excluded.brand_hint,
                updated_at = excluded.updated_at
            """,
            (guild_id, target.target_key, user_key, feedback.key, retailer, target.brand_hint[:120], now),
        )

    await conn.commit()
    prefix = "Updated your vote." if changed_vote else feedback.response
    return FeedbackRecordResult(True, False, changed_vote, f"{feedback.emoji} {prefix}")


async def insert_feedback_event(conn: Any, *, guild_id: int, target: DealFeedbackTarget, action: str, retailer: str, score_delta: int, user_key: str, created_at: str) -> None:
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
            action,
            score_delta,
            user_key,
            created_at,
        ),
    )


async def apply_feedback_delta(conn: Any, *, guild_id: int, target: DealFeedbackTarget, feedback: FeedbackAction, retailer: str, sign: int, updated_at: str) -> None:
    good = sign if feedback.column == "good_count" else 0
    bad = sign if feedback.column == "bad_count" else 0
    bad_brand = sign if feedback.column == "bad_brand_count" else 0
    flip = sign if feedback.column == "flip_count" else 0
    weak = sign if feedback.column == "weak_count" else 0
    score_delta = feedback.score_delta * sign

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
        (guild_id, target.target_key, retailer, target.title[:300], target.url[:800], target.brand_hint[:120], good, bad, bad_brand, flip, weak, score_delta, updated_at),
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
            (guild_id, retailer, target.brand_hint[:120], good, bad, bad_brand, flip, weak, score_delta, updated_at),
        )


async def apply_feedback_learning_to_cards(db, *, guild_id: int | None, cards: list[Any], fallback_retailer: str = "walmart") -> list[Any]:
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
        CREATE TABLE IF NOT EXISTS guild_deal_feedback_targets (
            token TEXT PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            target_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            brand_hint TEXT,
            source_label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_deal_feedback_user_votes (
            guild_id INTEGER NOT NULL,
            target_key TEXT NOT NULL,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            retailer TEXT NOT NULL,
            brand_hint TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, target_key, user_id)
        )
        """
    )
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
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_feedback_targets_expiry ON guild_deal_feedback_targets (expires_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_feedback_events_guild_created ON guild_deal_feedback_events (guild_id, created_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_feedback_user_votes_guild ON guild_deal_feedback_user_votes (guild_id, updated_at)")
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
