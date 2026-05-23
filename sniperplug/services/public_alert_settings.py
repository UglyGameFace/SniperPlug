from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sniperplug.models.deal import utc_now_iso

ALL_STORES_SENTINEL = "all"


@dataclass(frozen=True)
class PublicAlertSettings:
    enabled: bool = False
    enabled_sources: tuple[str, ...] = ()

    def allows_source(self, source_key: str | None, retailer: str | None = None) -> bool:
        if not self.enabled:
            return False
        if not self.enabled_sources:
            return True
        candidates = {normalize_store_key(value) for value in (source_key, retailer) if value}
        return any(candidate in self.enabled_sources for candidate in candidates)

    @property
    def store_text(self) -> str:
        return "all registered stores" if not self.enabled_sources else ", ".join(self.enabled_sources)


async def ensure_public_alert_settings_table(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_alert_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            enabled_sources_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def set_public_alert_settings(db, guild_id: int, *, enabled: bool, enabled_sources: tuple[str, ...]) -> None:
    await ensure_public_alert_settings_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    sources = tuple(sorted(set(enabled_sources)))
    await conn.execute(
        """
        INSERT INTO guild_public_alert_settings (guild_id, enabled, enabled_sources_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled = excluded.enabled,
            enabled_sources_json = excluded.enabled_sources_json,
            updated_at = excluded.updated_at
        """,
        (guild_id, int(enabled), json.dumps(list(sources)), now, now),
    )
    await conn.commit()


async def get_public_alert_settings(db, guild_id: int) -> PublicAlertSettings:
    await ensure_public_alert_settings_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT enabled, enabled_sources_json FROM guild_public_alert_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return PublicAlertSettings()
    raw_sources: Any
    try:
        raw_sources = json.loads(row["enabled_sources_json"] or "[]")
    except json.JSONDecodeError:
        raw_sources = []
    sources = tuple(
        normalize_store_key(source)
        for source in raw_sources
        if isinstance(source, str) and normalize_store_key(source) != ALL_STORES_SENTINEL
    )
    return PublicAlertSettings(enabled=bool(row["enabled"]), enabled_sources=tuple(sorted(set(sources))))


def parse_enabled_stores(raw: str | None, *, available_sources: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Parse a comma/space separated store list.

    Empty, `all`, or `*` means all stores. Unknown stores are preserved so the
    setting can be created before a future provider is registered.
    """
    if raw is None or not raw.strip():
        return ()
    normalized: list[str] = []
    tokens = [token.strip() for chunk in raw.split(",") for token in chunk.split()]
    for token in tokens:
        key = normalize_store_key(token)
        if not key:
            continue
        if key in {ALL_STORES_SENTINEL, "*", "any", "everyone"}:
            return ()
        normalized.append(key)
    return tuple(sorted(set(normalized)))


def normalize_store_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")
