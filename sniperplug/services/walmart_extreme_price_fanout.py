from __future__ import annotations

"""Walmart extreme-price leads, kept separate from verified markdown alerts."""

import asyncio
from collections import Counter
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

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


def extreme_rejection_reason(candidate: Any) -> str | None:
    """Explain the first failed gate without weakening exact-offer evidence.

    A None result is the *same* eligibility result used by public posting.
    Reasons are coarse and safe for a public server-owner health summary: no
    raw API payloads, item IDs, sellers, or per-product URLs are disclosed.
    """
    if candidate is None:
        return "missing_snapshot"
    if exact_offer_identity(candidate) is None:
        return "offer_or_seller_proof_missing"
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if str(attrs.get("exactDetailPriceProof", "")).strip().lower() != "yes":
        return "exact_price_proof_missing"
    item_id = str(attrs.get("exactDetailItemId") or "")
    url = getattr(candidate, "direct_product_url", None) or getattr(candidate, "product_url", "")
    if not item_id.isdigit() or walmart_item_id_from_url(url) != item_id:
        return "item_url_mismatch"
    if str(getattr(candidate, "retailer", "") or "").strip().lower() != "walmart":
        return "wrong_retailer"
    try:
        current = float(candidate.api_current_price)
        displayed = float(candidate.current_price)
    except (TypeError, ValueError, OverflowError):
        return "exact_price_missing"
    if not (0 < current <= MAX_PRICE_CENTS / 100 and abs(current - displayed) < 0.01):
        return "price_mismatch_or_over_limit"
    if candidate.can_add_to_cart is not True:
        return "not_cart_buyable"
    # Nonempty "false" from source metadata must not be treated as bool True.
    online = str(attrs.get("availableOnline") or "").strip().lower()
    if online in {"no", "false", "0", "off"}:
        return "unavailable_online"
    if candidate.is_member_only or candidate.is_checkout_price:
        return "restricted_checkout_price"
    stock = str(candidate.stock_status or "").strip().lower()
    if any(
        blocked in stock
        for blocked in (
            "sold out", "out of stock", "out-of-stock", "unavailable",
            "not available", "not in stock",
        )
    ):
        return "unavailable_stock"
    title = " ".join(
        str(value or "")
        for value in (candidate.title, candidate.parent_title)
    )
    if ACCESSORY.search(title):
        return "accessory_or_parts"
    if PRIMARY_PRODUCT.search(title) is None:
        return "not_recognized_primary_product"
    return None


def eligible_exact_extreme_candidate(candidate: Any) -> bool:
    """Allow a proved exact price without inventing an original price."""
    return extreme_rejection_reason(candidate) is None

BATCH_LIMIT = 40
FRESHNESS_MINUTES = 15
SETTINGS_TABLE = "walmart_extreme_price_alert_settings"
STATE_TABLE = "walmart_extreme_price_fanout_state"
_LOCK = asyncio.Lock()
_READY_CONNECTIONS: dict[int, Any] = {}
log = logging.getLogger("sniperplug.autoscan.extreme_price")

def _row_get(row: Any, column: str, index: int) -> Any:
    """Read both Turso mapping rows and SQLite positional/Row results."""
    if row is None:
        return None
    try:
        return row[column]
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return row[index]
    except (KeyError, IndexError, TypeError):
        pass
    return getattr(row, column, None)



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
    # Raw reference context can be untrusted in this lane; it must not appear
    # as a claimed original-price discount in an automatic lead.
    lead = ScoutLead(
        candidate=candidate,
        score=lead.score,
        reasons=tuple(reason for reason in lead.reasons if "price context" not in reason),
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
                value=f"Exact offer price: **${float(candidate.api_current_price):.2f}**. No comparison price or discount percentage is claimed.",
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
    return bool(row and int(_row_get(row, "enabled", 0) or 0))


async def set_extreme_alerts_enabled(db: Any, guild_id: int, enabled: bool) -> None:
    await ensure_extreme_tables(db)
    conn = db.require_conn()
    existing_cursor = await conn.execute(f"SELECT COUNT(*) AS enabled_count FROM {SETTINGS_TABLE} WHERE enabled = 1")
    previously_enabled = int(_row_get(await existing_cursor.fetchone(), "enabled_count", 0) or 0)
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


def delivery_requires_retry(result: Any) -> bool:
    """Do not mistake a fallback-channel note for a failed Discord delivery.

    PublicPostResult.errors contains both true errors and informational notes.
    Use the structured post/skip counts as the authoritative outcome. An
    unconfirmed post receipt is explicitly critical even after Discord sent it.
    """
    attempted = int(getattr(result, "attempted", 1) or 1)
    completed = sum(
        int(getattr(result, attr, 0) or 0)
        for attr in (
            "posted",
            "skipped_duplicate",
            "skipped_not_alertable",
            "skipped_disabled",
            "skipped_wrong_retailer",
        )
    )
    if completed < attempted:
        return True
    if any(str(note).startswith("CRITICAL:") for note in getattr(result, "errors", ())):
        return True
    return any(
        str(reason).startswith("category_error:")
        for reason in getattr(result, "not_alertable_reasons", ())
    )


async def extreme_price_health_summary(db: Any) -> str:
    """Explain the last 15 minutes of exact-price candidates, without polling Walmart.

    Bounded to a small sample from the same durable queue and statuses consumed
    by the optional sender. This is an observation, not an availability claim,
    delivery receipt, or a complete count of the retailer's catalog.
    """
    await ensure_extreme_tables(db)
    conn = db.require_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=FRESHNESS_MINUTES)).isoformat()
    cursor = await conn.execute(
        f"SELECT last_verified_at, last_item_id FROM {STATE_TABLE} "
        "WHERE state_key = 'walmart'"
    )
    state = await cursor.fetchone()
    if state is None:
        return "Extreme-price cursor is not initialized."
    last_seen = (
        str(_row_get(state, "last_verified_at", 0) or ""),
        str(_row_get(state, "last_item_id", 1) or ""),
    )
    limit = 60
    cursor = await conn.execute(
        f"SELECT item_id, verified_at, snapshot_json FROM {QUEUE_TABLE} "
        "WHERE status IN ('verified_no_reference', 'verified_under_threshold', "
        "'verified_reference') "
        "AND exact_current_cents BETWEEN 1 AND ? "
        "AND verified_at >= ? AND snapshot_json <> '' "
        "ORDER BY verified_at DESC, item_id DESC LIMIT ?",
        (MAX_PRICE_CENTS, cutoff, limit),
    )
    rows = await cursor.fetchall()
    qualified = 0
    unprocessed = 0
    rejected: Counter[str] = Counter()
    for row in rows:
        item_id = str(_row_get(row, "item_id", 0) or "")
        verified_at = str(_row_get(row, "verified_at", 1) or "")
        raw = _row_get(row, "snapshot_json", 2)
        try:
            reason = extreme_rejection_reason(_candidate_from_snapshot(raw))
        except (TypeError, ValueError, KeyError, AttributeError):
            reason = "unreadable_snapshot"
        if reason is not None:
            rejected[reason] += 1
            continue
        qualified += 1
        if (verified_at, item_id) > last_seen:
            unprocessed += 1
    capped = " (sample capped at 60)" if len(rows) >= limit else ""
    rejection_line = "; ".join(
        f"{count} {reason.replace('_', ' ')}"
        for reason, count in rejected.most_common(4)
    ) or "none"
    return (
        f"Last {FRESHNESS_MINUTES} min: **{len(rows)}** exact-verified queue "
        f"rows at $10 or less{capped}; **{qualified}** match primary-product, "
        f"seller, stock, offer and price checks; **{unprocessed}** are after "
        "the delivery cursor. These are candidates, not confirmed Discord posts. "
        f"Not eligible: {rejection_line}. "
        "This is a bounded sample, not proof of the entire live Walmart catalog."
    )


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
        conn = db.require_conn()
        opted_cursor = await conn.execute(
            f"SELECT guild_id FROM {SETTINGS_TABLE} WHERE enabled = 1"
        )
        opted_ids = {str(_row_get(row, "guild_id", 0)) for row in await opted_cursor.fetchall()}
        # Almost all servers keep this optional lane disabled. Never run the
        # full live-guild reconciliation on every 20-second worker cycle.
        if not opted_ids:
            return {"examined": 0, "eligible": 0, "posted": 0}
        cursor = await conn.execute(
            f"SELECT last_verified_at, last_item_id FROM {STATE_TABLE} WHERE state_key = 'walmart'"
        )
        state = await cursor.fetchone()
        if state is None:
            raise RuntimeError("Walmart extreme-price cursor missing")
        previous = (str(_row_get(state, "last_verified_at", 0)), str(_row_get(state, "last_item_id", 1)))
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
        enabled: list[int] | None = None
        for row in rows:
            item_id, verified_at, snapshot = (
                str(_row_get(row, "item_id", 0)),
                str(_row_get(row, "verified_at", 1)),
                str(_row_get(row, "snapshot_json", 2)),
            )
            examined += 1
            candidate = _candidate_from_snapshot(snapshot)
            card = build_extreme_price_card(candidate)
            if card is not None:
                eligible += 1
                if enabled is None:
                    live = await list_live_public_alert_guilds(
                        db, bot, required_retailer="walmart"
                    )
                    enabled = [
                        int(guild.guild_id)
                        for guild in live.guilds
                        if snowflake_text(guild.guild_id) in opted_ids
                    ]
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
                    if delivery_requires_retry(result):
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
                examined, eligible, posted, len(enabled or ()),
            )
        return {"examined": examined, "eligible": eligible, "posted": posted}
