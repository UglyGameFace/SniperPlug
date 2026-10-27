from __future__ import annotations

"""Walmart extreme-price leads, kept separate from verified markdown alerts."""

import re
from typing import Any
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sniperplug.services.autoscan_live_guild_reconciliation import list_live_public_alert_guilds
from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.low_price_scout import ScoutLead, build_scout_card, score_candidate
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE, _candidate_from_snapshot


from sniperplug.services.public_deal_quality import walmart_item_id_from_url
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity

MAX_PRICE_CENTS = 1000
PRIMARY_PRODUCT = re.compile(
    r"\b(?:galaxy\s+(?:s\d{2}|z\s*(?:fold|flip)|a\d{2})|s2\d\s*(?:fe|ultra|plus)?"
    r"|iphone\s*\d{2}|ipad\s*(?:pro|air|mini|\d+)|pixel\s*[6-9]"
    r"|macbook|laptop|playstation\s*[45]|ps[45]|xbox\s+(?:series|one)"
    r"|nintendo\s+switch|oled\s+tv|robot\s+vacuum)\b",
    re.IGNORECASE,
)
ACCESSORY = re.compile(
    r"\b(?:case|cover|protector|charger|cable|adapter|replacement|parts? only"
    r"|box only|mount|holder|remote control|display dummy)\b",
    re.IGNORECASE,
)


def eligible_exact_extreme_candidate(candidate: Any) -> bool:
    """Allow a real exact price without inventing a discount/reference price."""
    if candidate is None or exact_offer_identity(candidate) is None:
        return False
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if str(attrs.get("exactDetailPriceProof", "")).strip().lower() != "yes":
        return False
    item_id = str(attrs.get("exactDetailItemId") or "")
    url = getattr(candidate, "direct_product_url", None) or getattr(candidate, "product_url", "")
    if not item_id.isdigit() or walmart_item_id_from_url(url) != item_id:
        return False
    if str(getattr(candidate, "retailer", "") or "").strip().lower() != "walmart":
        return False
    try:
        current = float(candidate.api_current_price)
        displayed = float(candidate.current_price)
    except (TypeError, ValueError, OverflowError):
        return False
    if not (0 < current <= MAX_PRICE_CENTS / 100 and abs(current - displayed) < 0.01):
        return False
    if candidate.can_add_to_cart is not True:
        return False
    if candidate.is_member_only or candidate.is_checkout_price:
        return False
    stock = str(candidate.stock_status or "").lower()
    if any(x in stock for x in ("sold out", "out of stock", "unavailable")):
        return False
    title = " ".join(str(x or "") for x in (candidate.title, candidate.parent_title))
    return bool(PRIMARY_PRODUCT.search(title) and not ACCESSORY.search(title))

BATCH_LIMIT = 40
FRESHNESS_MINUTES = 15
SETTINGS_TABLE = "walmart_extreme_price_alert_settings"
STATE_TABLE = "walmart_extreme_price_fanout_state"
_LOCK = asyncio.Lock()
_READY_CONNECTIONS: dict[int, Any] = {}
log = logging.getLogger("sniperplug.autoscan.extreme_price")


def build_extreme_price_card(candidate: Any) -> Any | None:
    if not eligible_exact_extreme_candidate(candidate):
        return None
    lead = score_candidate(candidate)
    if lead is None:
        lead = ScoutLead(
            candidate=candidate,
            score=70,
            reasons=("recognizable primary product", "official Walmart exact-item price"),
        )
    card = build_scout_card(lead)
    card.url = candidate.direct_product_url or candidate.product_url
    card.embed.url = card.url
    card.embed.title = "Extreme exact-price find: " + str(candidate.title or "")[:155]
    # The private scout may show an untrusted raw MSRP. Do not repeat it in an
    # automatic alert when the source did not prove a reference/markdown.
    for index, field in enumerate(card.embed.fields):
        if str(field.name) == "💰 Scout price":
            card.embed.set_field_at(
                index,
                name="Walmart exact API price",
                value=f"Exact offer price: **${float(candidate.api_current_price):.2f}**. No verified reference price or discount.",
                inline=False,
            )
            break
    card.embed.add_field(
        name="Exact price, NOT a verified markdown",
        value=(
            "Walmart's official exact-item API returned $"
            + f"{float(candidate.api_current_price):.2f}"
            + " for this seller/offer/variant. No original price or discount is claimed. "
            + "Recheck condition, local availability, shipping and checkout price."
        ),
        inline=False,
    )
    card.embed.set_footer(text="Extreme exact-price lead; original price unverified; verify checkout")
    card.extreme_exact_candidate = candidate
    card.should_alert = True
    return card


def valid_extreme_price_card(card: Any) -> bool:
    """Recheck the provenance at the shared public-send boundary."""
    candidate = getattr(card, "extreme_exact_candidate", None)
    if not eligible_exact_extreme_candidate(candidate):
        return False
    try:
        return (
            str(card.selected_offer_id or "") == str(candidate.selected_offer_id or "")
            and abs(float(card.current_price) - float(candidate.api_current_price)) < 0.01
            and walmart_item_id_from_url(card.url)
            == str(candidate.variant_attributes.get("exactDetailItemId") or "")
        )
    except (ValueError, TypeError, OverflowError):
        return False


async def ensure_extreme_tables(db: Any) -> None:
    conn = db.require_conn()
    if _READY_CONNECTIONS.get(id(conn)) is conn:
        return
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} "
        "(guild_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0)"
    )
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {STATE_TABLE} "
        "(state_key TEXT PRIMARY KEY, last_verified_at TEXT NOT NULL, last_item_id TEXT NOT NULL)"
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=FRESHNESS_MINUTES)).isoformat()
    await conn.execute(
        f"INSERT INTO {STATE_TABLE} (state_key, last_verified_at, last_item_id) "
        "VALUES ('walmart', ?, '') ON CONFLICT(state_key) DO NOTHING",
        (cutoff,),
    )
    await conn.commit()
    _READY_CONNECTIONS[id(conn)] = conn


async def extreme_alerts_enabled(db: Any, guild_id: int) -> bool:
    await ensure_extreme_tables(db)
    cursor = await db.require_conn().execute(
        f"SELECT enabled FROM {SETTINGS_TABLE} WHERE guild_id = ?",
        (snowflake_text(guild_id),),
    )
    row = await cursor.fetchone()
    return bool(row and int(row[0]))


async def set_extreme_alerts_enabled(db: Any, guild_id: int, enabled: bool) -> None:
    await ensure_extreme_tables(db)
    conn = db.require_conn()
    existing_cursor = await conn.execute(f"SELECT COUNT(*) FROM {SETTINGS_TABLE} WHERE enabled = 1")
    previously_enabled = int((await existing_cursor.fetchone())[0] or 0)
    await conn.execute(
        f"INSERT INTO {SETTINGS_TABLE} (guild_id, enabled) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled = excluded.enabled",
        (snowflake_text(guild_id), int(bool(enabled))),
    )
    # Enabling the first server must not replay an old queue backlog.
    if enabled and not previously_enabled:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=FRESHNESS_MINUTES)).isoformat()
        await conn.execute(
            f"UPDATE {STATE_TABLE} SET last_verified_at = ?, last_item_id = '' WHERE state_key = 'walmart'",
            (cutoff,),
        )
    await conn.commit()


async def fanout_recent_extreme_walmart_prices(bot: Any) -> dict[str, int]:
    """Post only opt-in, exact-verified, unusually cheap primary products.

    This uses its own cursor and existing public-send dedupe/reservations.
    It never advances or changes the verified-markdown/DM fanout cursor.
    """
    db = getattr(bot, "db", None)
    if db is None or _LOCK.locked():
        return {"examined": 0, "eligible": 0, "posted": 0}
    async with _LOCK:
        await ensure_extreme_tables(db)
        live = await list_live_public_alert_guilds(db, bot, required_retailer="walmart")
        enabled = [
            int(guild.guild_id)
            for guild in live.guilds
            if await extreme_alerts_enabled(db, int(guild.guild_id))
        ]
        if not enabled:
            return {"examined": 0, "eligible": 0, "posted": 0}
        conn = db.require_conn()
        cursor = await conn.execute(
            f"SELECT last_verified_at, last_item_id FROM {STATE_TABLE} WHERE state_key = 'walmart'"
        )
        state = await cursor.fetchone()
        if state is None:
            raise RuntimeError("Walmart extreme-price cursor missing")
        previous = (str(state[0]), str(state[1]))
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=FRESHNESS_MINUTES)).isoformat()
        cursor = await conn.execute(
            f"SELECT item_id, verified_at, snapshot_json FROM {QUEUE_TABLE} "
            "WHERE status IN ('verified_no_reference', 'verified_under_threshold', 'verified_reference') "
            "AND exact_current_cents BETWEEN 1 AND ? "
            "AND verified_at >= ? AND snapshot_json <> '' "
            "AND (verified_at, item_id) > (?, ?) "
            "ORDER BY verified_at ASC, item_id ASC LIMIT ?",
            (MAX_PRICE_CENTS, cutoff, *previous, BATCH_LIMIT),
        )
        rows = await cursor.fetchall()
        next_cursor = previous
        examined = eligible = posted = 0
        for row in rows:
            item_id, verified_at, snapshot = str(row[0]), str(row[1]), str(row[2])
            examined += 1
            candidate = _candidate_from_snapshot(snapshot)
            card = build_extreme_price_card(candidate)
            if card is not None:
                eligible += 1
                for guild_id in enabled:
                    result = await maybe_post_public_deal_cards(
                        bot=bot,
                        guild_id=guild_id,
                        cards=[card],
                        source_label="walmart_extreme_exact_price",
                        fallback_retailer="walmart",
                        allow_verified_extreme=True,
                    )
                    posted += result.posted
                    if result.errors:
                        # Failed posts remain eligible next cycle. Existing durable
                        # receipts prevent double delivery to successful guilds.
                        log.error(
                            "Walmart extreme-price delivery failed guild=%s item=%s errors=%s",
                            guild_id, item_id, result.errors,
                        )
                        break
                else:
                    next_cursor = (verified_at, item_id)
                    continue
                break
            next_cursor = (verified_at, item_id)
        if next_cursor != previous:
            await conn.execute(
                f"UPDATE {STATE_TABLE} SET last_verified_at = ?, last_item_id = ? "
                "WHERE state_key = 'walmart'",
                next_cursor,
            )
            await conn.commit()
        if examined:
            log.info(
                "Walmart extreme exact-price examined=%s eligible=%s posted=%s destinations=%s",
                examined, eligible, posted, len(enabled),
            )
        return {"examined": examined, "eligible": eligible, "posted": posted}
