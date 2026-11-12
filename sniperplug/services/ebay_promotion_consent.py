from __future__ import annotations

"""Consent and marketing safeguards, isolated from Walmart preferences.

A developer credential or EPN account is not written messaging approval.
The eBay affiliate lane stays disabled without both a true consent state and
a separate explicit EPN messaging approval setting.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.ebay_browse_api import EbaySettings, EbayListing

USER_TABLE = "ebay_dm_promotion_consent"
GUILD_TABLE = "ebay_guild_promotion_consent"
DISCLOSURE = "Affiliate disclosure: SniperPlug may earn a commission from qualifying eBay purchases."


@dataclass(frozen=True)
class EbayGuildConsent:
    enabled: bool = False
    channel_id: int | None = None
    actor_id: int | None = None


async def ensure_ebay_consent(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {USER_TABLE} ("
        "user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, "
        "updated_at TEXT NOT NULL)"
    )
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {GUILD_TABLE} ("
        "guild_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, "
        "channel_id TEXT, actor_id TEXT, updated_at TEXT NOT NULL)"
    )
    await conn.commit()


def _snowflake(value: Any) -> str:
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Invalid Discord ID") from None
    if result < 1 or result >= 2**64:
        raise ValueError("Invalid Discord ID")
    return str(result)


async def save_user_consent(db: Any, user_id: int, enabled: bool) -> None:
    await ensure_ebay_consent(db)
    conn = db.require_conn()
    if not enabled:
        # Remove data on unsubscribe; absent row also means no consent.
        await conn.execute(f"DELETE FROM {USER_TABLE} WHERE user_id = ?", (_snowflake(user_id),))
    else:
        await conn.execute(
            f"INSERT INTO {USER_TABLE} (user_id, enabled, updated_at) "
            "VALUES (?, 1, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "enabled = 1, updated_at = excluded.updated_at",
            (_snowflake(user_id), datetime.now(timezone.utc).isoformat()),
        )
    await conn.commit()


async def user_consented(db: Any, user_id: int) -> bool:
    await ensure_ebay_consent(db)
    cursor = await db.require_conn().execute(
        f"SELECT enabled FROM {USER_TABLE} WHERE user_id = ?",
        (_snowflake(user_id),),
    )
    row = await cursor.fetchone()
    return bool(row and int(row[0]) == 1)


async def save_guild_consent(
    db: Any, *, guild_id: int, actor_id: int, channel_id: int | None, enabled: bool,
) -> None:
    await ensure_ebay_consent(db)
    conn = db.require_conn()
    # Explicit disable clears channel so stale permission cannot turn itself
    # back on after a deploy or a future admin action.
    await conn.execute(
        f"INSERT INTO {GUILD_TABLE} "
        "(guild_id, enabled, channel_id, actor_id, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled = excluded.enabled, "
        "channel_id = excluded.channel_id, actor_id = excluded.actor_id, "
        "updated_at = excluded.updated_at",
        (
            _snowflake(guild_id),
            int(bool(enabled)),
            _snowflake(channel_id) if enabled and channel_id else None,
            _snowflake(actor_id),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    await conn.commit()


async def guild_consent(db: Any, guild_id: int) -> EbayGuildConsent:
    await ensure_ebay_consent(db)
    cursor = await db.require_conn().execute(
        f"SELECT enabled, channel_id, actor_id FROM {GUILD_TABLE} WHERE guild_id = ?",
        (_snowflake(guild_id),),
    )
    row = await cursor.fetchone()
    if not row:
        return EbayGuildConsent()
    return EbayGuildConsent(
        enabled=bool(row[0]),
        channel_id=int(row[1]) if row[1] else None,
        actor_id=int(row[2]) if row[2] else None,
    )


def may_promote(
    settings: EbaySettings,
    *,
    opted_in: bool,
    channel_matches: bool = True,
) -> bool:
    """Hard fail-closed boundary for all future affiliate deliveries."""
    return bool(settings.affiliate_ready and opted_in and channel_matches)


def preview_text(listing: EbayListing, *, approved: bool) -> str:
    """Truthful sample, never mistaken for confirmed resale profitability."""
    price = f"USD {listing.price_cents / 100:,.2f}"
    shipping = (
        f"USD {listing.shipping_cents / 100:,.2f}"
        if listing.shipping_cents is not None else "unverified"
    )
    total = (
        f"USD {listing.delivered_cents / 100:,.2f}"
        if listing.delivered_cents is not None else "unknown"
    )
    return (
        f"{listing.title}\n"
        f"Current eBay ASKING price: {price} | Shipping: {shipping} | "
        f"Delivered: {total}\n"
        f"Condition: {listing.condition} | Seller: {listing.seller or 'unknown'}\n"
        "Not a sold comparable, verified resale value, or profit estimate.\n"
        + (DISCLOSURE if approved else "DEMO ONLY: Affiliate promotion is not approved or active.")
    )


def safe_affiliate_link(
    settings: EbaySettings, listing: EbayListing, *,
    opted_in: bool, channel_matches: bool = True,
) -> str:
    """Never derive tracking URLs, never substitute an untracked API link."""
    if not may_promote(settings, opted_in=opted_in, channel_matches=channel_matches):
        return ""
    return listing.affiliate_url
