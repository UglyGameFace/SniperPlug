from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from sniperplug.services.public_posting import normalize_retailer_key


@dataclass(frozen=True)
class PublicPostResult:
    attempted: int = 0
    posted: int = 0
    skipped_duplicate: int = 0
    skipped_not_alertable: int = 0
    skipped_disabled: int = 0
    skipped_wrong_retailer: int = 0
    errors: tuple[str, ...] = ()

    @property
    def any_activity(self) -> bool:
        return bool(self.posted or self.skipped_duplicate or self.errors)


async def maybe_post_public_deal_cards(
    *,
    bot: Any,
    guild_id: int | None,
    cards: list[Any],
    source_label: str,
) -> PublicPostResult:
    """Post alertable deal cards to the configured public channel.

    Manual scans and auto scans both use this path. Auto-scan interval gates only
    protect provider calls; public posting is controlled by /public_alerts and a
    separate duplicate guard so the same deal/offer/price cannot be blasted twice.
    """
    if guild_id is None or not cards:
        return PublicPostResult()

    db = getattr(bot, "db", None)
    if db is None:
        return PublicPostResult(errors=("public posting skipped: bot database unavailable",))

    config = await get_public_post_config(db, guild_id)
    if not config["enabled"] or not config["channel_id"]:
        return PublicPostResult(skipped_disabled=len(cards))

    channel = bot.get_channel(config["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(config["channel_id"])
        except Exception as exc:  # pragma: no cover - Discord network/runtime path
            return PublicPostResult(errors=(f"public channel lookup failed: {exc}",))
    if not hasattr(channel, "send"):
        return PublicPostResult(errors=("configured public alert channel is not sendable",))

    allowed_retailers = set(config["retailers"])
    posted = 0
    skipped_duplicate = 0
    skipped_not_alertable = 0
    skipped_wrong_retailer = 0
    errors: list[str] = []

    for card in cards:
        retailer = normalize_retailer_key(getattr(card, "retailer", None))
        if retailer not in allowed_retailers:
            skipped_wrong_retailer += 1
            continue
        if not bool(getattr(card, "should_alert", False)):
            skipped_not_alertable += 1
            continue
        deal_key = getattr(card, "public_post_key", None) or public_post_key(
            retailer=retailer,
            url=getattr(card, "url", ""),
            current_price=getattr(card, "current_price", None),
            selected_offer_id=getattr(card, "selected_offer_id", None),
            sku=getattr(card, "sku", None),
            upc=getattr(card, "upc", None),
        )
        reserved = await reserve_public_deal_post(db, guild_id=guild_id, retailer=retailer, deal_key=deal_key, source_label=source_label)
        if not reserved:
            skipped_duplicate += 1
            continue
        try:
            await channel.send(embed=card.embed)
        except Exception as exc:  # pragma: no cover - Discord network/runtime path
            await release_public_deal_reservation(db, guild_id=guild_id, deal_key=deal_key)
            errors.append(f"public post failed for {retailer}: {exc}")
            continue
        await mark_public_deal_posted(db, guild_id=guild_id, deal_key=deal_key)
        posted += 1

    return PublicPostResult(
        attempted=len(cards),
        posted=posted,
        skipped_duplicate=skipped_duplicate,
        skipped_not_alertable=skipped_not_alertable,
        skipped_disabled=0,
        skipped_wrong_retailer=skipped_wrong_retailer,
        errors=tuple(errors[:5]),
    )


def public_post_key(
    *,
    retailer: str,
    url: str,
    current_price: float | None = None,
    selected_offer_id: str | None = None,
    sku: str | None = None,
    upc: str | None = None,
) -> str:
    key_parts = [
        normalize_retailer_key(retailer),
        selected_offer_id or sku or upc or canonical_url_key(url),
        price_key(current_price),
    ]
    return ":".join(str(part) for part in key_parts if part)


def price_key(value: float | None) -> str:
    if value is None:
        return "price:unknown"
    return f"price:{value:.2f}"


def canonical_url_key(url: str) -> str:
    text = (url or "").strip()
    return text.split("?", 1)[0].rstrip("/") or "unknown"


async def get_public_post_config(db, guild_id: int) -> dict:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT enabled, retailers_json, channel_id FROM guild_public_alert_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {"enabled": False, "retailers": (), "channel_id": None}
    try:
        retailers = tuple(normalize_retailer_key(value) for value in json.loads(row["retailers_json"] or "[]"))
    except Exception:
        retailers = ()
    return {
        "enabled": bool(row["enabled"]),
        "retailers": retailers,
        "channel_id": int(row["channel_id"]) if row["channel_id"] else None,
    }


async def ensure_public_post_tables(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_alert_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            retailers_json TEXT NOT NULL DEFAULT '[]',
            channel_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_deal_posts (
            guild_id INTEGER NOT NULL,
            deal_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            source_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved',
            first_seen_at TEXT NOT NULL,
            posted_at TEXT,
            PRIMARY KEY (guild_id, deal_key)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_deal_posts_guild_retailer ON guild_public_deal_posts (guild_id, retailer)"
    )
    await conn.commit()


async def reserve_public_deal_post(db, *, guild_id: int, retailer: str, deal_key: str, source_label: str) -> bool:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await conn.execute(
        """
        INSERT OR IGNORE INTO guild_public_deal_posts (guild_id, deal_key, retailer, source_label, status, first_seen_at)
        VALUES (?, ?, ?, ?, 'reserved', ?)
        """,
        (guild_id, deal_key, normalize_retailer_key(retailer), source_label, now),
    )
    await conn.commit()
    return bool(getattr(cursor, "rowcount", 0))


async def mark_public_deal_posted(db, *, guild_id: int, deal_key: str) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE guild_public_deal_posts SET status = 'posted', posted_at = ? WHERE guild_id = ? AND deal_key = ?",
        (now, guild_id, deal_key),
    )
    await conn.commit()


async def release_public_deal_reservation(db, *, guild_id: int, deal_key: str) -> None:
    conn = db.require_conn()
    await conn.execute(
        "DELETE FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ? AND status = 'reserved'",
        (guild_id, deal_key),
    )
    await conn.commit()
