from __future__ import annotations

import json
import logging
from typing import Any

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.public_posting import normalize_retailer_key


log = logging.getLogger("sniperplug.public_alerts")
CHANNEL_PREFIX = "ch:"
PATCH_ATTR = "_sniperplug_text_channel_ids_installed"


def install_public_alert_text_id_patch() -> None:
    """Store Discord channel IDs as text, not numeric DB values.

    Discord snowflakes are large integers. Some DB/driver paths can round them
    when they travel through numeric/JSON layers. A rounded channel ID is what
    made the saved #walmart-deals channel become Unknown Channel.
    """
    try:
        from sniperplug.cogs import auto_scan_runner, public_alerts
        from sniperplug.services import public_deal_posts
    except Exception as exc:
        log.warning("Could not install public alert text-ID patch: %s", exc)
        return

    if getattr(public_alerts, PATCH_ATTR, False):
        # Still patch imported aliases in case a module was reloaded/imported later.
        auto_scan_runner.get_public_post_config = get_public_alert_config
        public_deal_posts.get_public_post_config = get_public_alert_config
        public_deal_posts.update_public_alert_channel_id = update_public_alert_channel_id
        return

    public_alerts.get_public_alert_config = get_public_alert_config
    public_alerts.set_public_alert_config = set_public_alert_config
    public_deal_posts.get_public_post_config = get_public_alert_config
    public_deal_posts.update_public_alert_channel_id = update_public_alert_channel_id
    # auto_scan_runner imported get_public_post_config directly at module import
    # time, so patch that module global too. Otherwise it keeps calling the old
    # int(row["channel_id"]) version and crashes on safe IDs like ch:123.
    auto_scan_runner.get_public_post_config = get_public_alert_config
    setattr(public_alerts, PATCH_ATTR, True)
    setattr(public_deal_posts, PATCH_ATTR, True)
    setattr(auto_scan_runner, PATCH_ATTR, True)
    log.info("Installed public alert text channel-id patch.")


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

    raw_channel_id = row["channel_id"]
    channel_id = decode_channel_id(raw_channel_id)
    if raw_channel_id and not str(raw_channel_id).startswith(CHANNEL_PREFIX) and channel_id is not None:
        await update_public_alert_channel_id(db, guild_id=guild_id, channel_id=channel_id)

    try:
        retailers = tuple(normalize_retailer_key(value) for value in json.loads(row["retailers_json"] or "[]"))
    except Exception:
        retailers = ()
    return {"enabled": bool(row["enabled"]), "retailers": retailers, "channel_id": channel_id}


async def set_public_alert_config(db: Any, *, guild_id: int, enabled: bool, retailers: tuple[str, ...], channel_id: int | str | None) -> None:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
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
            json.dumps([normalize_retailer_key(retailer) for retailer in retailers]),
            encode_channel_id(channel_id),
            now,
            now,
        ),
    )
    await conn.commit()


async def update_public_alert_channel_id(db: Any, *, guild_id: int, channel_id: int | str) -> None:
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
    try:
        return int(text)
    except (TypeError, ValueError):
        return None
