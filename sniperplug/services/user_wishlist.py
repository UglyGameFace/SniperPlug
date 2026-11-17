"""Per-user wishlists: plain product names a user wants deal alerts about.

This is the free-text counterpart to the curated systems that already exist:

* ``dm_product_interests`` — 31 curated product profiles (``iphone``,
  ``lego_sets``, ...). A user can only pick from that fixed list, and an
  unknown name is rejected as "could not identify".
* ``dm_deal_alerts`` keywords — up to 12 required words, but they only
  *narrow* the broad DM stream; they never say "tell me about this item".
* ``watchlist_seeds`` — bot-owned scan sharpeners per category, not per user.

A wishlist item is just a name: ``iphone 11``, ``dyson airwrap``,
``rtx 5070``. When a verified deal's title contains the whole phrase, the
item counts as explicit user intent in ``match_dm_deal`` — it bypasses the
category allowlist/mute veto exactly like a chosen product interest does —
but every hard guard stays hard: exact current/was price proof, the 20%
personal-alert floor, minimum score/savings, max price, required/excluded
keywords, dedupe and the daily cap. A wishlist never lowers a threshold
and never triggers its own scan; it matches against the verified-deal
stream the fanout already delivers.

Storage follows the existing per-user SQLite pattern from
``sniperplug.services.dm_deal_alerts``: one table, ``user_id TEXT`` key,
``db.require_conn()``, ``CREATE TABLE IF NOT EXISTS`` guarded by a
process-wide ready flag.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sniperplug.services.dm_product_interests import (
    normalize_match_text,
    phrase_in_text,
)

WISHLIST_TABLE = "user_wishlist_items"
MAX_WISHLIST_ITEMS_PER_USER = 20
MAX_WISHLIST_ITEM_LENGTH = 80

# Plain-words honesty note, shared by the cog: a wishlist never steers
# scanning. It only qualifies deals the bot's broad scan already found.
WISHLIST_SCAN_NOTE = (
    "Your wishlist doesn't steer the bot's scanning — a DM can only fire "
    "after the bot's broad scan finds the item as a verified deal."
)
_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class WishlistAddResult:
    added: bool
    item: str
    reason: str = ""
    count: int = 0

    @property
    def at_cap(self) -> bool:
        return not self.added and self.reason == "cap"


def normalize_wishlist_item(value: object) -> str:
    """Display form: trimmed, single-spaced, at most 80 chars."""

    text = " ".join(str(value or "").split())
    return text[:MAX_WISHLIST_ITEM_LENGTH].strip()


def wishlist_item_key(value: object) -> str:
    """Match key: the normalized phrase used for storage and matching."""

    return normalize_match_text(normalize_wishlist_item(value))


async def ensure_user_wishlist_tables(db: Any) -> None:
    """Initialize wishlist storage once per process."""

    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = db.require_conn()
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {WISHLIST_TABLE} (
                user_id TEXT NOT NULL,
                item TEXT NOT NULL,
                item_key TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, item_key)
            )
            """
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{WISHLIST_TABLE}_user "
            f"ON {WISHLIST_TABLE} (user_id, added_at)"
        )
        await conn.commit()
        _SCHEMA_READY = True


async def list_user_wishlist(db: Any, user_id: int) -> tuple[str, ...]:
    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT item FROM {WISHLIST_TABLE} WHERE user_id = ? ORDER BY added_at ASC",
        (str(int(user_id)),),
    )
    rows = await cursor.fetchall()
    return tuple(str(_row_get(row, "item", 0)) for row in rows)


async def list_wishlists_for_users(
    db: Any, user_ids: Iterable[int]
) -> dict[int, tuple[str, ...]]:
    """One query for a fanout batch: user id -> wishlist item display names."""

    ids = [int(user_id) for user_id in dict.fromkeys(user_ids)]
    if not ids:
        return {}
    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    placeholders = ", ".join("?" for _ in ids)
    cursor = await conn.execute(
        f"SELECT user_id, item FROM {WISHLIST_TABLE} "
        f"WHERE user_id IN ({placeholders}) ORDER BY user_id, added_at ASC",
        tuple(str(user_id) for user_id in ids),
    )
    rows = await cursor.fetchall()
    grouped: dict[int, list[str]] = {user_id: [] for user_id in ids}
    for row in rows:
        try:
            owner = int(_row_get(row, "user_id", 0))
        except (TypeError, ValueError):
            continue
        if owner in grouped:
            grouped[owner].append(str(_row_get(row, "item", 1)))
    return {user_id: tuple(items) for user_id, items in grouped.items()}


async def add_user_wishlist_item(
    db: Any, user_id: int, item: str
) -> WishlistAddResult:
    display = normalize_wishlist_item(item)
    key = wishlist_item_key(item)
    if not key:
        return WishlistAddResult(False, display, "empty")
    if len(key.split()) > 8:
        return WishlistAddResult(False, display, "too_long")
    await ensure_user_wishlist_tables(db)
    current = await list_user_wishlist(db, user_id)
    if any(wishlist_item_key(existing) == key for existing in current):
        return WishlistAddResult(False, display, "duplicate", len(current))
    if len(current) >= MAX_WISHLIST_ITEMS_PER_USER:
        return WishlistAddResult(False, display, "cap", len(current))
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {WISHLIST_TABLE} (user_id, item, item_key, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, item_key) DO NOTHING
        """,
        (
            str(int(user_id)),
            display,
            key,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    await conn.commit()
    return WishlistAddResult(True, display, "", len(current) + 1)


async def remove_user_wishlist_item(
    db: Any, user_id: int, item: str
) -> tuple[bool, str]:
    """Remove by display name or phrase; returns (removed, display name)."""

    key = wishlist_item_key(item)
    if not key:
        return False, normalize_wishlist_item(item)
    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT item FROM {WISHLIST_TABLE} WHERE user_id = ? AND item_key = ?",
        (str(int(user_id)), key),
    )
    row = await cursor.fetchone()
    if row is None:
        return False, normalize_wishlist_item(item)
    display = str(_row_get(row, "item", 0))
    await conn.execute(
        f"DELETE FROM {WISHLIST_TABLE} WHERE user_id = ? AND item_key = ?",
        (str(int(user_id)), key),
    )
    await conn.commit()
    return True, display


async def clear_user_wishlist(db: Any, user_id: int) -> int:
    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT COUNT(*) AS total FROM {WISHLIST_TABLE} WHERE user_id = ?",
        (str(int(user_id)),),
    )
    row = await cursor.fetchone()
    removed = _as_int(_row_get(row, "total", 0))
    await conn.execute(
        f"DELETE FROM {WISHLIST_TABLE} WHERE user_id = ?",
        (str(int(user_id)),),
    )
    await conn.commit()
    return removed


def match_wishlist_items(
    items: Iterable[str] | str | None,
    search_text: str,
) -> str:
    """Return the first wishlist item whose whole phrase appears in the text.

    Whole-phrase, word-boundary matching (``phrase_in_text``): ``iphone 11``
    matches "Apple iPhone 11 64GB" but not "iphone 110 case" and ``iphone``
    does not match "iphonex". Empty string means no match.
    """

    if items is None:
        return ""
    candidates = (items,) if isinstance(items, str) else tuple(items)
    for item in candidates:
        display = normalize_wishlist_item(item)
        if display and phrase_in_text(search_text, display):
            return display
    return ""


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
