"""Per-user saved Home Depot location (ZIP + optional store number).

Felix's complaint (2026-11-21): the Home Depot penny hunt modal asked for
a ZIP and store number every single time, and a broad query typed without
one was blocked by the SerpApi credit guard. This store follows the exact
pattern of :mod:`sniperplug.services.movie_user_location` (one row per
user, snowflake-as-text, upsert with a round-trip check) but is a sibling
table on purpose: a movie radius is not a Home Depot home store, and the
two preferences must never overwrite each other.

Rule for callers: an explicitly typed ZIP/store always wins for that run;
the saved value only fills blanks. Saving happens from the menu's
"My Home Depot store" entry and automatically when a hunt/search/stock
run is submitted with an explicit ZIP.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from sniperplug.services.movie_ticket_snowflake_store import snowflake_text


TABLE = "hd_user_location"

_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_STORE_RE = re.compile(r"^\d{3,6}$")

_SCHEMA_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class HdUserLocation:
    user_id: int
    zip_code: str
    store_id: str | None = None

    @property
    def label(self) -> str:
        if self.store_id:
            return f"ZIP {self.zip_code} · store {self.store_id}"
        return f"ZIP {self.zip_code}"


def normalize_hd_zip(value: Any) -> str:
    text = str(value or "").strip()
    if not _ZIP_RE.fullmatch(text):
        raise ValueError("Enter a valid 5-digit US ZIP code (ZIP+4 is also accepted).")
    return text[:5]


def normalize_hd_store_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not _STORE_RE.fullmatch(text):
        raise ValueError("Store number must be 3-6 digits, or left blank.")
    return text


async def ensure_hd_user_location_table(db: Any) -> None:
    if bool(getattr(db, "_hd_user_location_schema_ready", False)):
        return
    async with _SCHEMA_LOCK:
        if bool(getattr(db, "_hd_user_location_schema_ready", False)):
            return
        conn = db.require_conn()
        await conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                user_id TEXT PRIMARY KEY,
                zip_code TEXT NOT NULL,
                store_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.commit()
        setattr(db, "_hd_user_location_schema_ready", True)


async def get_hd_user_location(db: Any, *, user_id: int) -> HdUserLocation | None:
    await ensure_hd_user_location_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT user_id, zip_code, store_id FROM {TABLE} WHERE user_id = ?",
        (snowflake_text(user_id),),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    try:
        zip_code = normalize_hd_zip(_row_value(row, "zip_code", 1))
    except ValueError:
        return None
    return HdUserLocation(
        user_id=int(_row_value(row, "user_id", 0)),
        zip_code=zip_code,
        store_id=normalize_hd_store_id(_row_value(row, "store_id", 2)),
    )


async def save_hd_user_location(
    db: Any,
    *,
    user_id: int,
    zip_code: str,
    store_id: str | None = None,
) -> HdUserLocation:
    await ensure_hd_user_location_table(db)
    preference = HdUserLocation(
        user_id=int(snowflake_text(user_id)),
        zip_code=normalize_hd_zip(zip_code),
        store_id=normalize_hd_store_id(store_id),
    )
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {TABLE} (user_id, zip_code, store_id, created_at, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            zip_code = excluded.zip_code,
            store_id = excluded.store_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (snowflake_text(preference.user_id), preference.zip_code, preference.store_id),
    )
    await conn.commit()
    verified = await get_hd_user_location(db, user_id=preference.user_id)
    if verified != preference:
        raise RuntimeError("Home Depot location failed its exact database round-trip check.")
    return preference


async def clear_hd_user_location(db: Any, *, user_id: int) -> bool:
    await ensure_hd_user_location_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"DELETE FROM {TABLE} WHERE user_id = ?",
        (snowflake_text(user_id),),
    )
    await conn.commit()
    return bool(getattr(cursor, "rowcount", 0))


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        pass
    try:
        return row[index]
    except (KeyError, TypeError, IndexError):
        return None
