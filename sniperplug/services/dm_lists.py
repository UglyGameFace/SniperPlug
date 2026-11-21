"""Per-user DM lists: the items people send the bot in direct messages.

Felix's use case (2026-11-19): his girlfriend sends him a pile of cart
links — never an actual wishlist — and screenshots of carts and
wishlists go the same way. This is the one saved list those sends land
in: one rolling list per user, fed by the DM intake cog
(``sniperplug.cogs.dm_list_intake``), reopened from ``/menu`` → My
deals → My list, and handed off item-by-item to the real wishlist
(``sniperplug.services.user_wishlist``) when the user asks.

A row's status is the honesty contract of the whole feature:

* ``exact`` — the item was pinned by the exact verifier
  (``verify_single_exact_item``, the same path ``/walmart check``
  uses) and the stored price is the price that check proved.
* ``closest`` — a search candidate the verifier could not pin. The
  name is real search output; there is deliberately **no price** on a
  closest row, because no price was ever proven for it.
* ``unchecked`` — a pasted link pinned an item ID, but the check
  itself failed (provider error, timeout). The row keeps the ID so
  "Check prices now" can retry it; it carries no price either.
* ``unreadable`` — a picture the OCR step yielded nothing usable
  from. No name, no price; it exists so the user can see the picture
  was received and counted, not silently dropped.

Storage follows the per-user pattern from ``user_wishlist``: one
table, ``user_id TEXT`` key, ``db.require_conn()``, ``CREATE TABLE IF
NOT EXISTS`` behind a process-wide ready flag — plus a one-time row in
``sniperplug_data_migrations`` (the repo's marker table) recording
when the table was introduced.

Dedupe, in order: a pinned item ID beats everything (the same item
sent as a link and read from a screenshot is one row); then the
normalized product name; unreadable rows carry neither, so they
dedupe on their source reference (message + attachment), which keeps
a re-delivered message from doubling a picture while letting two
different unreadable pictures both appear.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sniperplug.services.dm_product_interests import normalize_match_text

DM_LIST_TABLE = "user_dm_list_items"
DM_LIST_MIGRATION_KEY = "20261119_dm_list_items_table"
MAX_DM_LIST_ITEMS_PER_USER = 50

STATUS_EXACT = "exact"
STATUS_CLOSEST = "closest"
STATUS_UNCHECKED = "unchecked"
STATUS_UNREADABLE = "unreadable"
DM_LIST_STATUSES = frozenset(
    {STATUS_EXACT, STATUS_CLOSEST, STATUS_UNCHECKED, STATUS_UNREADABLE}
)

# Rows a wishlist add can use: they carry a real product name. An
# unchecked row's name is a placeholder ("Item 425031866") and an
# unreadable row has no name at all — wishlisting either would create
# a wishlist entry that can never match a deal.
WISHLISTABLE_STATUSES = frozenset({STATUS_EXACT, STATUS_CLOSEST})

_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dm_list_name_key(value: object) -> str:
    """The dedupe key for a product name (the wishlist's normalizer)."""

    return normalize_match_text(str(value or ""))


@dataclass(frozen=True)
class DmListCandidate:
    """One row the intake wants to add, before storage dedupes it."""

    name: str
    status: str
    item_id: str | None = None
    price_cents: int | None = None
    product_url: str | None = None
    source: str = "link"  # "link" | "photo"
    source_ref: str = ""
    # The verified was-price, when the verifier proved one. Lets the
    # list say "was $X · N% off" for a real deal and "not on sale"
    # for everything else (Felix, 2026-11-19).
    reference_price_cents: int | None = None


@dataclass(frozen=True)
class DmListRow:
    id: int
    user_id: int
    name: str
    status: str
    item_id: str | None = None
    price_cents: int | None = None
    reference_price_cents: int | None = None
    product_url: str | None = None
    source: str = "link"
    source_ref: str = ""
    added_at: str = ""
    updated_at: str = ""

    @property
    def wishlistable(self) -> bool:
        return self.status in WISHLISTABLE_STATUSES and bool(self.name.strip())


@dataclass(frozen=True)
class DmListAddOutcome:
    added: tuple[DmListRow, ...] = ()
    duplicates: int = 0
    cap_reached: bool = False
    total: int = 0


async def ensure_dm_list_tables(db: Any) -> None:
    """Initialize DM-list storage once per process (+ migration marker)."""

    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = db.require_conn()
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {DM_LIST_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_id TEXT,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                price_cents INTEGER,
                product_url TEXT,
                source TEXT NOT NULL DEFAULT 'link',
                source_ref TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reference_price_cents INTEGER
            )
            """
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{DM_LIST_TABLE}_user "
            f"ON {DM_LIST_TABLE} (user_id, added_at)"
        )
        # Existing tables (created before the was-price column):
        # add it in place, the same guarded ALTER the wishlist uses.
        column_cursor = await conn.execute(f"PRAGMA table_info({DM_LIST_TABLE})")
        existing_columns = {
            str(_row_get(row, "name", 1) or "")
            for row in await column_cursor.fetchall()
        }
        if "reference_price_cents" not in existing_columns:
            await conn.execute(
                f"ALTER TABLE {DM_LIST_TABLE} "
                "ADD COLUMN reference_price_cents INTEGER"
            )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sniperplug_data_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            "INSERT INTO sniperplug_data_migrations (migration_key, applied_at) "
            "VALUES (?, ?) ON CONFLICT(migration_key) DO NOTHING",
            (DM_LIST_MIGRATION_KEY, _utc_now_iso()),
        )
        await conn.commit()
        _SCHEMA_READY = True


def _row_from_db(row: Any) -> DmListRow:
    return DmListRow(
        id=int(_row_get(row, "id", 0) or 0),
        user_id=int(_row_get(row, "user_id", 1) or 0),
        item_id=(str(_row_get(row, "item_id", 2)) if _row_get(row, "item_id", 2) else None),
        name=str(_row_get(row, "name", 3) or ""),
        status=str(_row_get(row, "status", 5) or ""),
        price_cents=_as_optional_int(_row_get(row, "price_cents", 6)),
        reference_price_cents=_as_optional_int(
            _row_get(row, "reference_price_cents", 12)
        ),
        product_url=(
            str(_row_get(row, "product_url", 7))
            if _row_get(row, "product_url", 7)
            else None
        ),
        source=str(_row_get(row, "source", 8) or "link"),
        source_ref=str(_row_get(row, "source_ref", 9) or ""),
        added_at=str(_row_get(row, "added_at", 10) or ""),
        updated_at=str(_row_get(row, "updated_at", 11) or ""),
    )


_COLUMNS = (
    "id, user_id, item_id, name, name_key, status, price_cents, "
    "product_url, source, source_ref, added_at, updated_at, "
    "reference_price_cents"
)


async def list_dm_list_items(db: Any, user_id: int) -> tuple[DmListRow, ...]:
    """The user's saved list, oldest first."""

    await ensure_dm_list_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM {DM_LIST_TABLE} "
        f"WHERE user_id = ? ORDER BY added_at ASC, id ASC",
        (str(int(user_id)),),
    )
    return tuple(_row_from_db(row) for row in await cursor.fetchall())


async def add_dm_list_items(
    db: Any, user_id: int, candidates: Iterable[DmListCandidate]
) -> DmListAddOutcome:
    """Append candidates to the user's list, deduped and capped.

    Duplicates are counted, not inserted. Once the list holds
    ``MAX_DM_LIST_ITEMS_PER_USER`` rows, further candidates are
    refused and ``cap_reached`` says so — the caller turns that into
    the plain "your list is full" note.
    """

    await ensure_dm_list_tables(db)
    existing = await list_dm_list_items(db, user_id)
    rows_by_item_id = {row.item_id: row for row in existing if row.item_id}
    known_name_keys = {
        dm_list_name_key(row.name) for row in existing if dm_list_name_key(row.name)
    }
    known_source_refs = {row.source_ref for row in existing if row.source_ref}
    total = len(existing)
    added: list[DmListRow] = []
    duplicates = 0
    cap_reached = False
    conn = db.require_conn()
    now = _utc_now_iso()
    for candidate in candidates:
        item_id = (candidate.item_id or "").strip() or None
        name_key = dm_list_name_key(candidate.name)
        source_ref = candidate.source_ref or ""
        if item_id is not None and item_id in rows_by_item_id:
            prior = rows_by_item_id[item_id]
            if candidate.status == STATUS_EXACT and prior.status != STATUS_EXACT:
                # The same item, now proven: a screenshot's closest
                # match upgraded by the real link (or a retry that
                # finally pinned it). Refresh the row in place and
                # surface it with this intake's new rows.
                await conn.execute(
                    f"UPDATE {DM_LIST_TABLE} SET name = ?, name_key = ?, "
                    f"status = ?, price_cents = ?, product_url = ?, "
                    f"reference_price_cents = ?, "
                    f"updated_at = ? WHERE id = ?",
                    (
                        str(candidate.name or prior.name),
                        dm_list_name_key(candidate.name or prior.name),
                        STATUS_EXACT,
                        candidate.price_cents,
                        candidate.product_url or prior.product_url,
                        candidate.reference_price_cents,
                        now,
                        prior.id,
                    ),
                )
                await conn.commit()
                upgraded = DmListRow(
                    id=prior.id,
                    user_id=prior.user_id,
                    name=str(candidate.name or prior.name),
                    status=STATUS_EXACT,
                    item_id=item_id,
                    price_cents=candidate.price_cents,
                    reference_price_cents=candidate.reference_price_cents,
                    product_url=candidate.product_url or prior.product_url,
                    source=prior.source,
                    source_ref=prior.source_ref,
                    added_at=prior.added_at,
                    updated_at=now,
                )
                rows_by_item_id[item_id] = upgraded
                added.append(upgraded)
                continue
            duplicates += 1
            continue
        if item_id is None and name_key and name_key in known_name_keys:
            duplicates += 1
            continue
        if (
            candidate.status == STATUS_UNREADABLE
            and source_ref
            and source_ref in known_source_refs
        ):
            duplicates += 1
            continue
        if total >= MAX_DM_LIST_ITEMS_PER_USER:
            cap_reached = True
            continue
        status = (
            candidate.status
            if candidate.status in DM_LIST_STATUSES
            else STATUS_UNCHECKED
        )
        cursor = await conn.execute(
            f"""
            INSERT INTO {DM_LIST_TABLE}
                (user_id, item_id, name, name_key, status, price_cents,
                 product_url, source, source_ref, added_at, updated_at,
                 reference_price_cents)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                str(int(user_id)),
                item_id,
                str(candidate.name or ""),
                name_key,
                status,
                candidate.price_cents,
                candidate.product_url,
                candidate.source or "link",
                source_ref,
                now,
                now,
                candidate.reference_price_cents,
            ),
        )
        # RETURNING, not cursor.lastrowid: the production libsql
        # cursor does not expose lastrowid (only the sqlite test
        # double does) — asking for it crashed this save in
        # production on 2026-11-19, after the insert had already
        # committed, leaving DM replies stuck mid-flight.
        inserted = await cursor.fetchone()
        row_id = int(_row_get(inserted, "id", 0) or 0)
        new_row = DmListRow(
            id=row_id,
            user_id=int(user_id),
            name=str(candidate.name or ""),
            status=status,
            item_id=item_id,
            price_cents=candidate.price_cents,
            reference_price_cents=candidate.reference_price_cents,
            product_url=candidate.product_url,
            source=candidate.source or "link",
            source_ref=source_ref,
            added_at=now,
            updated_at=now,
        )
        if item_id is not None:
            rows_by_item_id[item_id] = new_row
        if name_key:
            known_name_keys.add(name_key)
        if source_ref:
            known_source_refs.add(source_ref)
        total += 1
        added.append(new_row)
    if added:
        await conn.commit()
    return DmListAddOutcome(
        added=tuple(added),
        duplicates=duplicates,
        cap_reached=cap_reached,
        total=total,
    )


async def update_dm_list_item_check(
    db: Any,
    row_id: int,
    *,
    name: str | None = None,
    price_cents: int | None = None,
    reference_price_cents: int | None = None,
    status: str | None = None,
    product_url: str | None = None,
) -> DmListRow | None:
    """Refresh one row after a re-check; returns the stored row.

    Only fields the re-check actually proved are passed in; a field
    left ``None`` keeps its stored value, except ``price_cents`` and
    ``reference_price_cents`` — a re-check that pins the item always
    carries its fresh price truth, and a was-price the verifier no
    longer proves must clear rather than linger as a stale deal.
    """

    await ensure_dm_list_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM {DM_LIST_TABLE} WHERE id = ?", (int(row_id),)
    )
    stored = await cursor.fetchone()
    if stored is None:
        return None
    current = _row_from_db(stored)
    new_name = name if name else current.name
    new_status = (
        status if status in DM_LIST_STATUSES else current.status
    )
    new_price = price_cents if price_cents is not None else current.price_cents
    new_reference = reference_price_cents
    new_url = product_url if product_url else current.product_url
    now = _utc_now_iso()
    await conn.execute(
        f"UPDATE {DM_LIST_TABLE} SET name = ?, name_key = ?, status = ?, "
        f"price_cents = ?, reference_price_cents = ?, product_url = ?, "
        f"updated_at = ? WHERE id = ?",
        (
            new_name,
            dm_list_name_key(new_name),
            new_status,
            new_price,
            new_reference,
            new_url,
            now,
            int(row_id),
        ),
    )
    await conn.commit()
    return DmListRow(
        id=current.id,
        user_id=current.user_id,
        name=new_name,
        status=new_status,
        item_id=current.item_id,
        price_cents=new_price,
        reference_price_cents=new_reference,
        product_url=new_url,
        source=current.source,
        source_ref=current.source_ref,
        added_at=current.added_at,
        updated_at=now,
    )


async def remove_dm_list_items(
    db: Any, user_id: int, item_ids: Iterable[int]
) -> int:
    """Delete specific rows from one user's list; returns how many went.

    Scoped by user_id in the DELETE itself, so ids from someone else's
    list can never be removed through this path.
    """

    ids = sorted({int(value) for value in item_ids})
    if not ids:
        return 0
    await ensure_dm_list_tables(db)
    conn = db.require_conn()
    placeholders = ", ".join("?" for _ in ids)
    cursor = await conn.execute(
        f"SELECT COUNT(*) AS n FROM {DM_LIST_TABLE} "
        f"WHERE user_id = ? AND id IN ({placeholders})",
        (int(user_id), *ids),
    )
    row = await cursor.fetchone()
    count = int(_row_get(row, "n", 0) or 0)
    if count:
        await conn.execute(
            f"DELETE FROM {DM_LIST_TABLE} "
            f"WHERE user_id = ? AND id IN ({placeholders})",
            (int(user_id), *ids),
        )
        await conn.commit()
    return count


async def clear_dm_list_items(db: Any, user_id: int) -> int:
    """Delete every row on one user's list; returns how many went."""

    await ensure_dm_list_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT COUNT(*) AS n FROM {DM_LIST_TABLE} WHERE user_id = ?",
        (int(user_id),),
    )
    row = await cursor.fetchone()
    count = int(_row_get(row, "n", 0) or 0)
    if count:
        await conn.execute(
            f"DELETE FROM {DM_LIST_TABLE} WHERE user_id = ?",
            (int(user_id),),
        )
        await conn.commit()
    return count


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


def _as_optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
