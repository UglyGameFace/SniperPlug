from __future__ import annotations

from dataclasses import dataclass

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.opportunity_watchlist import OPPORTUNITY_CATEGORIES, OpportunityCategory, category_for_title

CATEGORY_MODE_PRIORITY = "priority"
CATEGORY_MODE_NORMAL = "normal"
CATEGORY_MODE_MUTED = "muted"
VALID_CATEGORY_MODES = {CATEGORY_MODE_PRIORITY, CATEGORY_MODE_NORMAL, CATEGORY_MODE_MUTED}


@dataclass(frozen=True)
class CategoryDecision:
    category_key: str
    category_label: str
    mode: str
    action: str
    reason: str


def normalize_category_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"boost", "favorite", "watch", "wanted", "priority"}:
        return CATEGORY_MODE_PRIORITY
    if mode in {"hide", "muted", "mute", "ignore", "snooze"}:
        return CATEGORY_MODE_MUTED
    return CATEGORY_MODE_NORMAL


def category_rows() -> list[OpportunityCategory]:
    return sorted(OPPORTUNITY_CATEGORIES, key=lambda c: (c.label.lower(), c.key))


def valid_category_keys() -> set[str]:
    return {category.key for category in OPPORTUNITY_CATEGORIES}


async def ensure_deal_category_preference_table(db) -> None:
    conn = db.require_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_deal_category_preferences (
            guild_id INTEGER NOT NULL,
            category_key TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'normal',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, category_key)
        )
    """)
    await conn.commit()


async def set_category_preference(db, guild_id: int, category_key: str, mode: str) -> None:
    key = str(category_key or "").strip().lower()
    safe_mode = normalize_category_mode(mode)
    if key not in valid_category_keys():
        raise ValueError(f"Unknown deal category: {category_key}")
    await ensure_deal_category_preference_table(db)
    conn = db.require_conn()
    await conn.execute(
        """
        INSERT INTO guild_deal_category_preferences (guild_id, category_key, mode, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(guild_id, category_key)
        DO UPDATE SET mode = excluded.mode, updated_at = CURRENT_TIMESTAMP
        """,
        (int(guild_id), key, safe_mode),
    )
    await conn.commit()


async def get_category_preferences(db, guild_id: int) -> dict[str, str]:
    await ensure_deal_category_preference_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT category_key, mode FROM guild_deal_category_preferences WHERE guild_id = ?",
        (int(guild_id),),
    )
    rows = await cursor.fetchall()
    return {str(row["category_key"]): normalize_category_mode(str(row["mode"])) for row in rows}


def category_for_card(card: DealCard) -> OpportunityCategory | None:
    title = str(getattr(card, "label", "") or "")
    embed = getattr(card, "embed", None)
    if not title and embed is not None:
        title = str(getattr(embed, "title", "") or "")
    if not title and embed is not None:
        title = str(getattr(embed, "description", "") or "")
    return category_for_title(title)


def is_extreme_card(card: DealCard) -> bool:
    try:
        if float(getattr(card, "discount", 0) or 0) >= 70:
            return True
    except Exception:
        pass
    try:
        if int(getattr(card, "score", 0) or 0) >= 170:
            return True
    except Exception:
        pass
    embed = getattr(card, "embed", None)
    text = ""
    if embed is not None:
        text = " ".join(
            str(part or "")
            for part in [
                getattr(embed, "title", ""),
                getattr(embed, "description", ""),
                *[getattr(field, "value", "") for field in getattr(embed, "fields", [])],
            ]
        ).lower()
    return "90%+" in text or "nuclear" in text or "extreme" in text


def decide_category(card: DealCard, preferences: dict[str, str]) -> CategoryDecision:
    category = category_for_card(card)
    if category is None:
        return CategoryDecision(
            category_key="unknown",
            category_label="Unknown / uncategorized",
            mode=CATEGORY_MODE_NORMAL,
            action="allow",
            reason="Unknown category is not blocked. Strong markdown/proof can still post.",
        )

    mode = normalize_category_mode(preferences.get(category.key, CATEGORY_MODE_NORMAL))
    if mode == CATEGORY_MODE_PRIORITY:
        return CategoryDecision(
            category_key=category.key,
            category_label=category.label,
            mode=mode,
            action="boost",
            reason=f"Priority category: {category.label}.",
        )

    if mode == CATEGORY_MODE_MUTED:
        if is_extreme_card(card):
            return CategoryDecision(
                category_key=category.key,
                category_label=category.label,
                mode=mode,
                action="allow_extreme",
                reason=f"Muted category override: {category.label}, but markdown/score is extreme so SniperPlug will not miss it.",
            )
        return CategoryDecision(
            category_key=category.key,
            category_label=category.label,
            mode=mode,
            action="suppress",
            reason=f"Muted category: {category.label}. Normal deals stay out of the public feed.",
        )

    return CategoryDecision(
        category_key=category.key,
        category_label=category.label,
        mode=CATEGORY_MODE_NORMAL,
        action="allow",
        reason=f"Normal category: {category.label}.",
    )


def apply_category_preferences(cards: list[DealCard], preferences: dict[str, str]) -> tuple[list[DealCard], list[DealCard], list[str]]:
    allowed: list[DealCard] = []
    suppressed: list[DealCard] = []
    notes: list[str] = []

    for card in cards:
        decision = decide_category(card, preferences)
        setattr(card, "deal_category_key", decision.category_key)
        setattr(card, "deal_category_label", decision.category_label)
        setattr(card, "deal_category_mode", decision.mode)
        setattr(card, "deal_category_action", decision.action)

        embed = getattr(card, "embed", None)
        if embed is not None and not any(str(field.name or "") == "🏷️ Deal Category" for field in embed.fields):
            embed.add_field(
                name="🏷️ Deal Category",
                value=f"**{decision.category_label}** • mode: **{decision.mode}**\n{decision.reason}",
                inline=False,
            )

        if decision.action == "boost":
            try:
                card.score = int(getattr(card, "score", 0) or 0) + 25
            except Exception:
                pass
            allowed.append(card)
        elif decision.action == "suppress":
            suppressed.append(card)
        else:
            allowed.append(card)

        if decision.reason not in notes:
            notes.append(decision.reason)

    allowed.sort(key=lambda c: int(getattr(c, "score", 0) or 0), reverse=True)
    return allowed, suppressed, notes[:8]


def format_category_catalog(preferences: dict[str, str] | None = None) -> str:
    prefs = preferences or {}
    rows: list[str] = []
    for category in category_rows():
        mode = normalize_category_mode(prefs.get(category.key, CATEGORY_MODE_NORMAL))
        marker = "⭐" if mode == CATEGORY_MODE_PRIORITY else "🙈" if mode == CATEGORY_MODE_MUTED else "▫️"
        rows.append(f"{marker} `{category.key}` — {category.label}")
    return "\n".join(rows)
