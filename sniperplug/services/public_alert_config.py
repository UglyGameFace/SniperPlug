from __future__ import annotations

import json
from typing import Any

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.public_posting import normalize_retailer_key


CHANNEL_PREFIX = "ch:"


async def ensure_public_alert_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_alert_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            retailers_json TEXT NOT NULL DEFAULT '[]',
            channel_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def get_public_alert_config(db: Any, guild_id: int) -> dict[str, Any]:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT enabled, retailers_json, channel_id FROM guild_public_alert_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    if not row:
        now = utc_now_iso()
        await conn.execute(
            "INSERT INTO guild_public_alert_settings (guild_id, enabled, retailers_json, channel_id, created_at, updated_at) VALUES (?, 0, '[]', NULL, ?, ?)",
            (guild_id, now, now),
        )
        await conn.commit()
        return {"enabled": False, "retailers": (), "channel_id": None}

    try:
        retailers = tuple(
            retailer
            for retailer in (normalize_retailer_key(value) for value in json.loads(row["retailers_json"] or "[]"))
            if retailer
        )
    except Exception:
        retailers = ()
    channel_id = decode_channel_id(row["channel_id"])
    if row["channel_id"] and channel_id is not None and encode_channel_id(row["channel_id"]) != row["channel_id"]:
        await set_public_alert_channel_id(db, guild_id=guild_id, channel_id=channel_id)
    return {"enabled": bool(row["enabled"]), "retailers": retailers, "channel_id": channel_id}


async def set_public_alert_config(db: Any, *, guild_id: int, enabled: bool, retailers: tuple[str, ...], channel_id: int | str | None) -> None:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    normalized_retailers = tuple(retailer for retailer in (normalize_retailer_key(retailer) for retailer in retailers) if retailer)
    await conn.execute(
        """
        INSERT INTO guild_public_alert_settings (guild_id, enabled, retailers_json, channel_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled = excluded.enabled,
            retailers_json = excluded.retailers_json,
            channel_id = excluded.channel_id,
            updated_at = excluded.updated_at
        """,
        (
            guild_id,
            int(enabled),
            json.dumps(list(dict.fromkeys(normalized_retailers))),
            encode_channel_id(channel_id),
            now,
            now,
        ),
    )
    await conn.commit()


async def set_public_alert_channel_id(db: Any, *, guild_id: int, channel_id: int | str) -> None:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    await conn.execute(
        "UPDATE guild_public_alert_settings SET channel_id = ?, updated_at = ? WHERE guild_id = ?",
        (encode_channel_id(channel_id), utc_now_iso(), guild_id),
    )
    await conn.commit()


def encode_channel_id(value: int | str | None) -> str | None:
    decoded = decode_channel_id(value)
    return f"{CHANNEL_PREFIX}{decoded}" if decoded is not None else None


def decode_channel_id(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.startswith(CHANNEL_PREFIX):
        text = text[len(CHANNEL_PREFIX) :]
    text = text.strip().replace("<#", "").replace(">", "")
    if text.startswith("#"):
        text = text[1:]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
