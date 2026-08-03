from __future__ import annotations

import json
from typing import Any

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.public_posting import normalize_retailer_key


CHANNEL_PREFIX = "ch:"
HP_RETAILER_MIGRATION = "20260802_enable_hp_for_existing_walmart_public_alerts"
TARGET_RETAILER_MIGRATION = "20260802_enable_target_for_existing_walmart_public_alerts"


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
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sniperplug_data_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()
    await _migrate_existing_walmart_alerts_to_retailer(
        db,
        migration_key=HP_RETAILER_MIGRATION,
        retailer="hp",
    )
    await _migrate_existing_walmart_alerts_to_retailer(
        db,
        migration_key=TARGET_RETAILER_MIGRATION,
        retailer="target",
    )


async def _migrate_existing_walmart_alerts_to_retailer(
    db: Any,
    *,
    migration_key: str,
    retailer: str,
) -> int:
    """Enroll enabled Walmart destinations in a newly introduced free source once."""

    conn = db.require_conn()
    marker = await conn.execute(
        "SELECT 1 FROM sniperplug_data_migrations WHERE migration_key = ? LIMIT 1",
        (migration_key,),
    )
    if await marker.fetchone() is not None:
        return 0

    cursor = await conn.execute(
        "SELECT guild_id, retailers_json FROM guild_public_alert_settings WHERE enabled = 1"
    )
    updated = 0
    normalized_new = normalize_retailer_key(retailer)
    for row in await cursor.fetchall():
        try:
            retailers = [
                key
                for key in (
                    normalize_retailer_key(value)
                    for value in json.loads(row["retailers_json"] or "[]")
                )
                if key
            ]
        except Exception:
            continue
        if "walmart" not in retailers or normalized_new in retailers:
            continue
        retailers.append(normalized_new)
        await conn.execute(
            "UPDATE guild_public_alert_settings SET retailers_json = ?, updated_at = ? "
            "WHERE CAST(guild_id AS TEXT) = ?",
            (
                json.dumps(list(dict.fromkeys(retailers))),
                utc_now_iso(),
                snowflake_text(row["guild_id"]),
            ),
        )
        updated += 1

    await conn.execute(
        "INSERT INTO sniperplug_data_migrations (migration_key, applied_at) VALUES (?, ?) "
        "ON CONFLICT(migration_key) DO NOTHING",
        (migration_key, utc_now_iso()),
    )
    await conn.commit()
    return updated


async def get_public_alert_config(db: Any, guild_id: int) -> dict[str, Any]:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    guild_param = snowflake_text(guild_id)
    cursor = await conn.execute(
        "SELECT enabled, retailers_json, channel_id FROM guild_public_alert_settings "
        "WHERE CAST(guild_id AS TEXT) = ?",
        (guild_param,),
    )
    row = await cursor.fetchone()
    if not row:
        now = utc_now_iso()
        await conn.execute(
            "INSERT INTO guild_public_alert_settings "
            "(guild_id, enabled, retailers_json, channel_id, created_at, updated_at) "
            "VALUES (?, 0, '[]', NULL, ?, ?)",
            (guild_param, now, now),
        )
        await conn.commit()
        return {"enabled": False, "retailers": (), "channel_id": None}

    try:
        retailers = tuple(
            retailer
            for retailer in (
                normalize_retailer_key(value)
                for value in json.loads(row["retailers_json"] or "[]")
            )
            if retailer
        )
    except Exception:
        retailers = ()
    channel_id = decode_channel_id(row["channel_id"])
    if (
        row["channel_id"]
        and channel_id is not None
        and encode_channel_id(row["channel_id"]) != row["channel_id"]
    ):
        await set_public_alert_channel_id(
            db,
            guild_id=int(guild_param),
            channel_id=channel_id,
        )
    return {
        "enabled": bool(row["enabled"]),
        "retailers": retailers,
        "channel_id": channel_id,
    }


async def set_public_alert_config(
    db: Any,
    *,
    guild_id: int,
    enabled: bool,
    retailers: tuple[str, ...],
    channel_id: int | str | None,
) -> None:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    guild_param = snowflake_text(guild_id)
    normalized_retailers = tuple(
        retailer
        for retailer in (
            normalize_retailer_key(retailer) for retailer in retailers
        )
        if retailer
    )
    await conn.execute(
        """
        INSERT INTO guild_public_alert_settings
            (guild_id, enabled, retailers_json, channel_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled = excluded.enabled,
            retailers_json = excluded.retailers_json,
            channel_id = excluded.channel_id,
            updated_at = excluded.updated_at
        """,
        (
            guild_param,
            int(enabled),
            json.dumps(list(dict.fromkeys(normalized_retailers))),
            encode_channel_id(channel_id),
            now,
            now,
        ),
    )
    await conn.commit()


async def set_public_alert_channel_id(
    db: Any,
    *,
    guild_id: int,
    channel_id: int | str,
) -> None:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    await conn.execute(
        "UPDATE guild_public_alert_settings SET channel_id = ?, updated_at = ? "
        "WHERE CAST(guild_id AS TEXT) = ?",
        (
            encode_channel_id(channel_id),
            utc_now_iso(),
            snowflake_text(guild_id),
        ),
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
