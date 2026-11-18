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
keywords, dedupe and the daily cap. A wishlist never lowers a threshold;
it matches against the verified-deal stream the fanout already delivers.

An item may carry a target price (``target_price_cents``). The target is
one more hard guard, not a threshold change: when the matched item has a
target, the DM fires only when the verified price is at or under it.

Wishlist terms also steer scanning, inside the existing budget: the
distinct terms across all users join the catalog hunt's price-sniper
priority pool (see ``list_wishlist_steering_terms``), so they are checked
on the priority cadence instead of waiting for the slow general rotation.
Steering spends no extra API calls — a steered term takes a priority slot
that already exists — and it never removes a route; the hot list and the
general rotation keep their slots, just shared with the extra terms.

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
# Steering bound: at most this many distinct wishlist terms join the
# catalog priority pool, ranked by how many users asked for each term.
# The cap keeps the priority rotation short enough that every term in it
# is still revisited quickly, and it bounds the blast radius if wishlist
# usage grows faster than the scan budget.
MAX_WISHLIST_STEERING_TERMS = 50

# Plain-words honesty note, shared by the cog: wishlist terms get extra
# priority checks inside the existing scan, but a DM can only ever fire
# after the broad scan finds the item as a verified deal.
WISHLIST_SCAN_NOTE = (
    "Your wishlist items also get priority checks in the bot's broad scan, "
    "but a DM can only fire after the broad scan finds the item as a "
    "verified deal."
)
_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()


class WishlistEntry(str):
    """One wishlist item: its display name, plus its optional target price.

    A ``str`` subclass so every existing consumer of the plain display
    name (fanout grouping, matching, menu counts) keeps working byte-for-
    byte, while the DM matcher can also read ``target_price_cents``
    (``None`` = no target; DM on any match, the original behaviour).
    """

    target_price_cents: int | None

    def __new__(
        cls, item: object, target_price_cents: int | None = None
    ) -> "WishlistEntry":
        entry = super().__new__(cls, str(item))
        entry.target_price_cents = (
            int(target_price_cents)
            if target_price_cents is not None and int(target_price_cents) > 0
            else None
        )
        return entry


@dataclass(frozen=True)
class WishlistAddResult:
    added: bool
    item: str
    reason: str = ""
    count: int = 0
    updated: bool = False
    target_price_cents: int | None = None

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
                target_price_cents INTEGER,
                PRIMARY KEY (user_id, item_key)
            )
            """
        )
        # Additive migration, the repo's PRAGMA pattern: tables created
        # before target prices existed gain the column in place; their
        # rows keep working with NULL = no target.
        column_cursor = await conn.execute(f"PRAGMA table_info({WISHLIST_TABLE})")
        columns = {
            str(_row_get(row, "name", 1) or "")
            for row in await column_cursor.fetchall()
        }
        if "target_price_cents" not in columns:
            await conn.execute(
                f"ALTER TABLE {WISHLIST_TABLE} ADD COLUMN target_price_cents INTEGER"
            )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{WISHLIST_TABLE}_user "
            f"ON {WISHLIST_TABLE} (user_id, added_at)"
        )
        await conn.commit()
        _SCHEMA_READY = True


async def list_user_wishlist_entries(
    db: Any, user_id: int
) -> tuple[WishlistEntry, ...]:
    """The user's wishlist items with their target prices, oldest first."""

    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT item, target_price_cents FROM {WISHLIST_TABLE} "
        f"WHERE user_id = ? ORDER BY added_at ASC",
        (str(int(user_id)),),
    )
    rows = await cursor.fetchall()
    return tuple(
        WishlistEntry(
            str(_row_get(row, "item", 0)),
            _as_optional_int(_row_get(row, "target_price_cents", 1)),
        )
        for row in rows
    )


async def list_user_wishlist(db: Any, user_id: int) -> tuple[str, ...]:
    entries = await list_user_wishlist_entries(db, user_id)
    return tuple(str(entry) for entry in entries)


async def list_wishlists_for_users(
    db: Any, user_ids: Iterable[int]
) -> dict[int, tuple[str, ...]]:
    """One query for a fanout batch: user id -> wishlist entries.

    Entries are ``WishlistEntry`` (a ``str``), so callers that only need
    the display names see exactly the values they saw before, and the DM
    matcher can also read each item's target price.
    """

    ids = [int(user_id) for user_id in dict.fromkeys(user_ids)]
    if not ids:
        return {}
    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    placeholders = ", ".join("?" for _ in ids)
    cursor = await conn.execute(
        f"SELECT user_id, item, target_price_cents FROM {WISHLIST_TABLE} "
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
            grouped[owner].append(
                WishlistEntry(
                    str(_row_get(row, "item", 1)),
                    _as_optional_int(_row_get(row, "target_price_cents", 2)),
                )
            )
    return {user_id: tuple(items) for user_id, items in grouped.items()}


async def list_wishlist_steering_terms(
    db: Any, *, cap: int = MAX_WISHLIST_STEERING_TERMS
) -> tuple[str, ...]:
    """Distinct wishlist terms across all users, for scan steering.

    Terms are the stored match keys — deduplicated and normalized exactly
    the way matching normalizes them — ranked by how many distinct users
    asked for each, ties broken by term so the result is deterministic.
    Capped so the priority rotation stays short; a term drops out on its
    own when its last holder removes it. Read fresh on every catalog
    pass, so an add or remove steers the scan without a restart.
    """

    await ensure_user_wishlist_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT item_key, COUNT(DISTINCT user_id) AS holders FROM {WISHLIST_TABLE} "
        f"GROUP BY item_key ORDER BY holders DESC, item_key ASC LIMIT ?",
        (max(1, int(cap)),),
    )
    rows = await cursor.fetchall()
    return tuple(
        term
        for term in (str(_row_get(row, "item_key", 0) or "") for row in rows)
        if term
    )


async def add_user_wishlist_item(
    db: Any,
    user_id: int,
    item: str,
    *,
    target_price_cents: int | None = None,
) -> WishlistAddResult:
    display = normalize_wishlist_item(item)
    key = wishlist_item_key(item)
    if not key:
        return WishlistAddResult(False, display, "empty")
    if len(key.split()) > 8:
        return WishlistAddResult(False, display, "too_long")
    target = _as_optional_int(target_price_cents)
    await ensure_user_wishlist_tables(db)
    current = await list_user_wishlist_entries(db, user_id)
    existing = next(
        (entry for entry in current if wishlist_item_key(entry) == key), None
    )
    if existing is not None:
        if target is None:
            # A bare re-add changes nothing — in particular it never
            # clears a target the user already set for the item.
            return WishlistAddResult(
                False,
                str(existing),
                "duplicate",
                len(current),
                target_price_cents=existing.target_price_cents,
            )
        # Re-adding with a target is how a target is set or changed: the
        # row is updated in place, never duplicated, and the update is
        # allowed even when the wishlist is at the item cap.
        conn = db.require_conn()
        await conn.execute(
            f"UPDATE {WISHLIST_TABLE} SET target_price_cents = ? "
            f"WHERE user_id = ? AND item_key = ?",
            (target, str(int(user_id)), key),
        )
        await conn.commit()
        return WishlistAddResult(
            False,
            str(existing),
            "updated",
            len(current),
            updated=True,
            target_price_cents=target,
        )
    if len(current) >= MAX_WISHLIST_ITEMS_PER_USER:
        return WishlistAddResult(False, display, "cap", len(current))
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {WISHLIST_TABLE} (user_id, item, item_key, added_at, target_price_cents)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, item_key) DO NOTHING
        """,
        (
            str(int(user_id)),
            display,
            key,
            datetime.now(timezone.utc).isoformat(),
            target,
        ),
    )
    await conn.commit()
    return WishlistAddResult(
        True, display, "", len(current) + 1, target_price_cents=target
    )


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


def match_wishlist_entry(
    items: Iterable[str] | str | None,
    search_text: str,
) -> WishlistEntry | None:
    """Return the first wishlist entry whose whole phrase appears in the text.

    Whole-phrase, word-boundary matching (``phrase_in_text``): ``iphone 11``
    matches "Apple iPhone 11 64GB" but not "iphone 110 case" and ``iphone``
    does not match "iphonex". The returned entry keeps the item's target
    price (plain-string items match with no target). ``None`` = no match.
    """

    if items is None:
        return None
    candidates = (items,) if isinstance(items, str) else tuple(items)
    for item in candidates:
        display = normalize_wishlist_item(item)
        if display and phrase_in_text(search_text, display):
            target = (
                item.target_price_cents
                if isinstance(item, WishlistEntry)
                else None
            )
            return WishlistEntry(display, target)
    return None


def match_wishlist_items(
    items: Iterable[str] | str | None,
    search_text: str,
) -> str:
    """The matched item's display name, or "" when nothing matches."""

    entry = match_wishlist_entry(items, search_text)
    return str(entry) if entry is not None else ""


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


def _as_optional_int(value: Any) -> int | None:
    """A stored target price: a positive int, or None for "no target"."""

    try:
        number = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
