from __future__ import annotations

from dataclasses import dataclass

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.opportunity_watchlist import OPPORTUNITY_CATEGORIES, OpportunityCategory, category_for_title

CATEGORY_MODE_PRIORITY = "priority"
CATEGORY_MODE_NORMAL = "normal"
CATEGORY_MODE_MUTED = "muted"


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


async def reset_category_preferences(db, guild_id: int) -> None:
    await ensure_deal_category_preference_table(db)
    conn = db.require_conn()
    await conn.execute("DELETE FROM guild_deal_category_preferences WHERE guild_id = ?", (int(guild_id),))
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


async def apply_preset(db, guild_id: int, preset: str) -> None:
    preset_key = str(preset or "").strip().lower()
    presets = {
        "deal_week": {
            CATEGORY_MODE_PRIORITY: {
                "mobile_accessories", "apple", "brand_direct_electronics", "ssds", "tools",
                "motor_oil", "fragrance_beauty", "gold_jewelry", "smart_home", "office_school",
                "viral_gadgets", "toys_collectibles", "home_kitchen",
            },
            CATEGORY_MODE_MUTED: set(),
        },
        "flip_focus": {
            CATEGORY_MODE_PRIORITY: {
                "brand_direct_electronics", "apple", "gpus", "cpus", "ram", "ssds", "gold_jewelry",
                "watches", "fragrance_beauty", "tools", "toys_collectibles", "shoes_apparel",
                "mobile_accessories",
            },
            CATEGORY_MODE_MUTED: {"grocery_pantry", "household_essentials", "baby_kids", "pet_supplies"},
        },
        "daily_essentials": {
            CATEGORY_MODE_PRIORITY: {"household_essentials", "grocery_pantry", "baby_kids", "pet_supplies", "motor_oil"},
            CATEGORY_MODE_MUTED: set(),
        },
    }
    selected = presets.get(preset_key)
    if selected is None:
        raise ValueError(f"Unknown category preset: {preset}")

    await ensure_deal_category_preference_table(db)
    conn = db.require_conn()
    for mode, keys in selected.items():
        for key in keys:
            if key in valid_category_keys():
                await conn.execute(
                    """
                    INSERT INTO guild_deal_category_preferences (guild_id, category_key, mode, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(guild_id, category_key)
                    DO UPDATE SET mode = excluded.mode, updated_at = CURRENT_TIMESTAMP
                    """,
                    (int(guild_id), key, mode),
                )
    await conn.commit()


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
    return False


def decide_category(card: DealCard, preferences: dict[str, str]) -> CategoryDecision:
    category = category_for_card(card)
    if category is None:
        return CategoryDecision("unknown", "Unknown / uncategorized", CATEGORY_MODE_NORMAL, "allow", "Unknown category is not blocked. Strong markdown/proof can still post.")

    mode = normalize_category_mode(preferences.get(category.key, CATEGORY_MODE_NORMAL))
    if mode == CATEGORY_MODE_PRIORITY:
        return CategoryDecision(category.key, category.label, mode, "boost", f"Priority category: {category.label}.")
    if mode == CATEGORY_MODE_MUTED:
        if is_extreme_card(card):
            return CategoryDecision(category.key, category.label, mode, "allow_extreme", f"Muted category override: {category.label}, but markdown/score is extreme so SniperPlug will not miss it.")
        return CategoryDecision(category.key, category.label, mode, "suppress", f"Muted category: {category.label}. Normal deals stay out of the public feed.")
    return CategoryDecision(category.key, category.label, CATEGORY_MODE_NORMAL, "allow", f"Normal category: {category.label}.")


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


def category_mode_marker(mode: str) -> str:
    safe = normalize_category_mode(mode)
    if safe == CATEGORY_MODE_PRIORITY:
        return "⭐"
    if safe == CATEGORY_MODE_MUTED:
        return "🙈"
    return "▫️"


def format_category_page(preferences: dict[str, str], *, page: int = 0, page_size: int = 20) -> str:
    rows = category_rows()
    start = max(0, int(page)) * page_size
    chunk = rows[start:start + page_size]
    if not chunk:
        return "No categories on this page."
    lines = []
    for category in chunk:
        mode = normalize_category_mode(preferences.get(category.key, CATEGORY_MODE_NORMAL))
        lines.append(f"{category_mode_marker(mode)} `{category.key}` — {category.label}")
    return "\n".join(lines)


def category_page_count(page_size: int = 20) -> int:
    rows = category_rows()
    return max(1, (len(rows) + page_size - 1) // page_size)
