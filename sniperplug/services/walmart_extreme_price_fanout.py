from __future__ import annotations

"""Walmart extreme-price leads, kept separate from verified markdown alerts."""

import asyncio
from collections import Counter
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.autoscan_live_guild_reconciliation import list_live_public_alert_guilds
from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.low_price_scout import ScoutLead, build_scout_card
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE, _candidate_from_snapshot
from sniperplug.services.public_deal_quality import walmart_item_id_from_url
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity
from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference
from sniperplug.services.walmart_extreme_price_signals import (
    ACCESSORY,
    primary_product_evidence,
    public_extreme_value_evidence,
)

MAX_PRICE_CENTS = 1000

def extreme_rejection_reason(candidate: Any) -> str | None:
    """Explain the first failed gate without weakening exact-offer evidence.

    A None result is the *same* eligibility result used by public posting.
    Reasons are coarse and safe for a public server-owner health summary: no
    raw API payloads, item IDs, sellers, or per-product URLs are disclosed.
    """
    if candidate is None:
        return "missing_snapshot"
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if str(attrs.get("exactDetailPriceProof", "")).strip().lower() != "yes":
        return "exact_price_proof_missing"
    item_id = str(attrs.get("exactDetailItemId") or "")
    url = getattr(candidate, "direct_product_url", None) or getattr(candidate, "product_url", "")
    if not item_id.isdigit() or walmart_item_id_from_url(url) != item_id:
        return "item_url_mismatch"
    if str(getattr(candidate, "retailer", "") or "").strip().lower() != "walmart":
        return "wrong_retailer"
    if exact_offer_identity(candidate) is None:
        return "offer_or_seller_proof_missing"
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
    trusted_reference = _trusted_reference(candidate)
    if primary_product_evidence(
        candidate, trusted_reference=trusted_reference
    ) is None:
        return "not_recognized_primary_product"
    if public_extreme_value_evidence(
        candidate, trusted_reference=trusted_reference
    ) is None:
        return "insufficient_value_evidence"
    return None


def eligible_exact_extreme_candidate(candidate: Any) -> bool:
    """Allow a proved exact price without inventing an original price."""
    return extreme_rejection_reason(candidate) is None

BATCH_LIMIT = 40
FRESHNESS_MINUTES = 15
SETTINGS_TABLE = "walmart_extreme_price_alert_settings"
STATE_TABLE = "walmart_extreme_price_fanout_state"
PENDING_TABLE = "walmart_extreme_price_guild_pending"
MAX_PENDING_GUILDS_PER_OFFER = 128
MAX_REPLAY_PER_CYCLE = 32
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
    evidence = public_extreme_value_evidence(
        candidate, trusted_reference=_trusted_reference(candidate)
    )
    # Do not recycle scout search-route and "cheap price" score reasons.
    # Public reasons must describe the actual exact-item evidence.
    reasons = (
        ("high-value product type", "official Walmart exact-item price")
        if evidence == "high_value_hardware"
        else ("trusted reference indicates unusual value", "official Walmart exact-item price")
    )
    lead = ScoutLead(candidate=candidate, score=70, reasons=reasons)
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
    # Outbox is used only for a qualified offer with a temporarily absent
    # opted-in destination. Existing sender receipts remain authoritative.
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {PENDING_TABLE} "
        "(guild_id TEXT NOT NULL, item_id TEXT NOT NULL, verified_at TEXT NOT NULL, "
        "snapshot_json TEXT NOT NULL, "
        "PRIMARY KEY (guild_id, item_id, verified_at))"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{PENDING_TABLE}_time "
        f"ON {PENDING_TABLE} (verified_at)"
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
    # Do not acknowledge an opt-out while this worker is still in the middle
    # of delivering a just-selected exact offer to that same server.
    async with _LOCK:
        await _set_extreme_alerts_enabled_locked(db, guild_id, enabled)


async def _set_extreme_alerts_enabled_locked(
    db: Any, guild_id: int, enabled: bool
) -> None:
    await ensure_extreme_tables(db)
    conn = db.require_conn()
    existing_cursor = await conn.execute(f"SELECT COUNT(*) AS enabled_count FROM {SETTINGS_TABLE} WHERE enabled = 1")
    previously_enabled = int(_row_get(await existing_cursor.fetchone(), "enabled_count", 0) or 0)
    await conn.execute(
        f"INSERT INTO {SETTINGS_TABLE} (guild_id, enabled) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled = excluded.enabled",
        (snowflake_text(guild_id), int(bool(enabled))),
    )
    if not enabled:
        # Explicit opt-out cancels undelivered leads; never replay after opt-out.
        await conn.execute(
            f"DELETE FROM {PENDING_TABLE} WHERE guild_id = ?",
            (snowflake_text(guild_id),),
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


async def extreme_price_health_summary(db: Any, guild_id: int | None = None) -> str:
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
    pending_detail = ""
    if guild_id is not None:
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS n FROM {PENDING_TABLE} "
            "WHERE guild_id = ? AND verified_at >= ?",
            (snowflake_text(guild_id), cutoff),
        )
        pending = int(_row_get(await cursor.fetchone(), "n", 0) or 0)
        pending_detail = (
            f" Pending recovery for this server: **{pending}**"
            " (previously missed due to temporary live-route absence)."
        )
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
        f"{pending_detail}"
    )


async def _record_missing_guild_deliveries(
    conn: Any,
    *,
    missing_guild_ids: set[str],
    item_id: str,
    verified_at: str,
    snapshot_json: str,
) -> None:
    """Persist missing recipients before advancing the global cursor.

    This intentionally fails closed if there are too many absent recipients
    to queue safely. A cursor that did not move can be retried after recovery.
    """
    if not missing_guild_ids:
        return
    if len(missing_guild_ids) > MAX_PENDING_GUILDS_PER_OFFER:
        raise RuntimeError(
            f"Too many absent extreme-price destinations: "
            f"{len(missing_guild_ids)} > {MAX_PENDING_GUILDS_PER_OFFER}"
        )
    for guild_id in sorted(missing_guild_ids):
        await conn.execute(
            f"INSERT INTO {PENDING_TABLE} "
            "(guild_id, item_id, verified_at, snapshot_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, item_id, verified_at) DO NOTHING",
            (guild_id, item_id, verified_at, snapshot_json),
        )
    await conn.commit()


async def _replay_missing_guild_deliveries(
    bot: Any,
    conn: Any,
    opted_ids: set[str],
    cutoff: str,
) -> tuple[int, Any | None]:
    """Retry absent guilds only while the exact official snapshot is unchanged.

    The existing public post sender still owns delivery receipts and dedupe.
    Never replay on expired or changed exact-price evidence, even if a guild
    reappears, and do not manufacture a new Walmart API price.
    """
    cursor = await conn.execute(f"SELECT 1 FROM {PENDING_TABLE} LIMIT 1")
    if await cursor.fetchone() is None:
        return 0, None
    await conn.execute(f"DELETE FROM {PENDING_TABLE} WHERE verified_at < ?", (cutoff,))
    await conn.commit()

    live = await list_live_public_alert_guilds(
        bot.db, bot, required_retailer="walmart"
    )
    live_ids = {
        snowflake_text(guild.guild_id)
        for guild in live.guilds
        if snowflake_text(guild.guild_id) in opted_ids
    }
    if not live_ids:
        return 0, live

    posted = 0
    remaining = MAX_REPLAY_PER_CYCLE
    # Stay below SQLite variable limits and bound the total number of replays.
    sorted_ids = sorted(live_ids)
    for start in range(0, len(sorted_ids), 200):
        if remaining <= 0:
            break
        part = sorted_ids[start : start + 200]
        placeholders = ",".join("?" for _ in part)
        cursor = await conn.execute(
            f"SELECT guild_id, item_id, verified_at, snapshot_json FROM {PENDING_TABLE} "
            f"WHERE verified_at >= ? AND guild_id IN ({placeholders}) "
            "ORDER BY verified_at ASC, item_id ASC LIMIT ?",
            (cutoff, *part, remaining),
        )
        for row in await cursor.fetchall():
            remaining -= 1
            guild_id = str(_row_get(row, "guild_id", 0))
            item_id = str(_row_get(row, "item_id", 1))
            verified_at = str(_row_get(row, "verified_at", 2))
            snapshot = str(_row_get(row, "snapshot_json", 3))
            cursor_now = await conn.execute(
                f"SELECT verified_at, snapshot_json, status FROM {QUEUE_TABLE} "
                "WHERE item_id = ?",
                (item_id,),
            )
            current = await cursor_now.fetchone()
            same_proof = (
                current is not None
                and str(_row_get(current, "verified_at", 0)) == verified_at
                and str(_row_get(current, "snapshot_json", 1)) == snapshot
                and str(_row_get(current, "status", 2)) in (
                    "verified_no_reference", "verified_under_threshold",
                    "verified_reference",
                )
            )
            card = build_extreme_price_card(
                _candidate_from_snapshot(snapshot)
            ) if same_proof else None
            if card is not None:
                try:
                    result = await maybe_post_public_deal_cards(
                        bot=bot,
                        guild_id=int(guild_id),
                        cards=[card],
                        source_label="walmart_extreme_exact_price",
                        fallback_retailer="walmart",
                        allow_verified_extreme=True,
                    )
                except Exception:
                    log.exception(
                        "Walmart extreme pending replay failed guild=%s item=%s",
                        guild_id, item_id,
                    )
                    continue
                posted += int(getattr(result, "posted", 0) or 0)
                if delivery_requires_retry(result):
                    log.warning(
                        "Walmart extreme pending replay retained guild=%s item=%s",
                        guild_id, item_id,
                    )
                    continue
            # Item changed, expired, became ineligible, or delivery/duplicate
            # handling completed: clear its replay record, never send stale proof.
            await conn.execute(
                f"DELETE FROM {PENDING_TABLE} WHERE guild_id = ? "
                "AND item_id = ? AND verified_at = ?",
                (guild_id, item_id, verified_at),
            )
            await conn.commit()
    return posted, live


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
        replayed, replay_live = await _replay_missing_guild_deliveries(
            bot, conn, opted_ids, cutoff,
        )
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
        examined = eligible = 0
        posted = replayed
        enabled: list[int] | None = (
            [
                int(guild.guild_id)
                for guild in replay_live.guilds
                if snowflake_text(guild.guild_id) in opted_ids
            ]
            if replay_live is not None else None
        )
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
                if not enabled:
                    # Replaying pending tickets may also return an empty live
                    # guild set. Never let that cached empty result bypass the
                    # no-recipient guard or advance the shared cursor.
                    log.warning(
                        "Walmart extreme-price eligible item has no currently "
                        "live opted-in destination; cursor retained item=%s "
                        "opted_in_count=%s",
                        item_id, len(opted_ids),
                    )
                    break
                # Persist a bounded, expiring ticket for opted-in guilds that
                # were absent from reconciliation before acknowledging the
                # global cursor after any live destination receives this item.
                missing = opted_ids - {snowflake_text(gid) for gid in enabled}
                if missing:
                    await _record_missing_guild_deliveries(
                        conn,
                        missing_guild_ids=missing,
                        item_id=item_id,
                        verified_at=verified_at,
                        snapshot_json=snapshot,
                    )
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
