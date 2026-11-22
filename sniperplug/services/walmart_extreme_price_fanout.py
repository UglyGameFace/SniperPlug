from __future__ import annotations

"""Walmart extreme-price leads, kept separate from verified markdown alerts."""

import asyncio
from collections import Counter
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from sniperplug.services import walmart_global_deal_fanout as legacy
from sniperplug.services.autoscan_live_guild_reconciliation import list_live_public_alert_guilds
from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.dm_deal_alerts import (
    clear_dm_delivery_failures,
    dm_receipt_exists,
    list_enabled_dm_deal_alert_preferences,
    record_dm_delivery_failure,
)
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_delivery_policy import (
    dm_delivery_lock,
    evaluate_dm_delivery_gate,
    record_dm_delivery_receipt,
)
from sniperplug.services.dm_delivery_telemetry import (
    record_user_dm_decision,
    record_user_dm_outcome,
)
from sniperplug.services.low_price_scout import ScoutLead, build_scout_card
from sniperplug.services.public_deal_posts import (
    card_deal_key,
    maybe_post_public_deal_cards,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    VERIFIED_PAGE_PRICE_STATUS,
    _candidate_from_snapshot,
    _QueueClaim,
    page_observed_price,
)
from sniperplug.services.public_deal_quality import walmart_item_id_from_url
from sniperplug.providers.registry import provider_registry
from sniperplug.services.walmart_exact_queue_runtime import _fetch_exact_candidate_off_loop
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity
from sniperplug.services.walmart_exact_price_enrichment import (
    _positive_number,
    _trusted_reference,
)
from sniperplug.services.walmart_extreme_price_signals import (
    ABSOLUTE_EXTREME_MAX_PRICE,
    WALMART_FIRST_PARTY_SELLERS,
    exact_price_lead_evidence,
    is_accessory_title,
    listed_price_anomaly,
    primary_product_evidence,
)
from sniperplug.services.underprice_lead import (
    build_underprice_lead_card,
    underprice_lead_reference,
)
from sniperplug.services.stock_status import is_unavailable_stock

# Only <=$10 substantial hardware qualifies without a trusted markdown.
# Walmart's unverified MSRP cannot establish a price error.
MAX_PRICE_CENTS = int(ABSOLUTE_EXTREME_MAX_PRICE * 100)
# A tiny live move between verification and send is repricing noise, not a
# dead deal. Tolerate moves up to $0.25 or 1% of the verified price,
# whichever is larger; the eligibility re-check below still decides
# whether the live price is an extreme lead, so a jump back toward the
# list price still fails there. The displayed (snapshot) price is never
# off by more than the tolerance.
LIVE_PRICE_TOLERANCE_CENTS = 25
LIVE_PRICE_TOLERANCE_RATIO = 0.01

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
    if not (current > 0 and abs(current - displayed) < 0.01):
        return "price_mismatch_or_over_limit"
    if candidate.can_add_to_cart is not True:
        return "not_cart_buyable"
    # Nonempty "false" from source metadata must not be treated as bool True.
    online = str(attrs.get("availableOnline") or "").strip().lower()
    if online in {"no", "false", "0", "off"}:
        return "unavailable_online"
    if candidate.is_member_only or candidate.is_checkout_price:
        return "restricted_checkout_price"
    if is_unavailable_stock(candidate.stock_status):
        return "unavailable_stock"
    title = " ".join(
        str(value or "")
        for value in (candidate.title, candidate.parent_title)
    )
    if is_accessory_title(title):
        return "accessory_or_parts"
    trusted_reference = _trusted_reference(candidate)
    if exact_price_lead_evidence(
        candidate, trusted_reference=trusted_reference
    ) is not None:
        return None
    if current > MAX_PRICE_CENTS / 100:
        # Above $10 the only accepted evidence is an extreme anomaly against
        # Walmart's own listed price; nothing else is claimed for pricier items.
        if listed_price_anomaly(candidate) is not None:
            return None
        return "price_mismatch_or_over_limit"
    if primary_product_evidence(
        candidate, trusted_reference=trusted_reference
    ) is None:
        return "not_recognized_primary_product"
    return "insufficient_value_evidence"
    return None


def eligible_exact_extreme_candidate(candidate: Any) -> bool:
    """Allow a proved exact price without inventing an original price."""
    return extreme_rejection_reason(candidate) is None


def page_price_rejection_reason(candidate: Any) -> str | None:
    """Explain why a page-observed price is not an extreme-lane lead.

    Mirrors extreme_rejection_reason, but the price proof is the verified
    product-page buy box instead of the official API. No API price is ever
    required or consulted here: a candidate that has one belongs to the
    exact-price lane, never to this one.
    """
    if candidate is None:
        return "missing_snapshot"
    if _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    ) is not None:
        return "api_price_present"
    page_price = page_observed_price(candidate)
    if page_price is None:
        return "page_identity_not_verified"
    attrs = getattr(candidate, "variant_attributes", None) or {}
    item_id = str(attrs.get("exactDetailItemId") or "")
    url = getattr(candidate, "direct_product_url", None) or getattr(candidate, "product_url", "")
    if not item_id.isdigit() or walmart_item_id_from_url(url) != item_id:
        return "item_url_mismatch"
    if str(getattr(candidate, "retailer", "") or "").strip().lower() != "walmart":
        return "wrong_retailer"
    if exact_offer_identity(candidate) is None:
        return "offer_or_seller_proof_missing"
    if candidate.can_add_to_cart is not True:
        return "not_cart_buyable"
    # Nonempty "false" from source metadata must not be treated as bool True.
    online = str(attrs.get("availableOnline") or "").strip().lower()
    if online in {"no", "false", "0", "off"}:
        return "unavailable_online"
    if candidate.is_member_only or candidate.is_checkout_price:
        return "restricted_checkout_price"
    if is_unavailable_stock(candidate.stock_status):
        return "unavailable_stock"
    title = " ".join(
        str(value or "")
        for value in (candidate.title, candidate.parent_title)
    )
    if is_accessory_title(title):
        return "accessory_or_parts"
    # Above $10 a page price qualifies only as an extreme anomaly against
    # Walmart's own listed price, exactly as for API-priced rows (#322):
    # the candidate snapshot may still carry the API's list/MSRP price
    # even though the API returned no current price. The list price is
    # never taken from the page itself. At or below $10 the substantial
    # primary-product signal qualifies, as before.
    if page_price > MAX_PRICE_CENTS / 100:
        if listed_price_anomaly(candidate, current_price=page_price) is not None:
            return None
        return "page_price_over_extreme_limit"
    if primary_product_evidence(candidate, trusted_reference=None) is None:
        return "not_recognized_primary_product"
    return None


def eligible_page_price_extreme_candidate(candidate: Any) -> bool:
    """Allow a verified page-observed price as an extreme-lane lead only."""
    return page_price_rejection_reason(candidate) is None

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



def _normalized_seller_name(candidate: Any) -> str:
    return " ".join(
        str(getattr(candidate, "seller_name", "") or "").lower().split()
    )


def _same_seller_name(original: Any, refreshed: Any) -> bool:
    """True when two fetches name the same seller.

    Walmart's own fetch paths disagree on the first-party seller's
    string form: the search/detail path records "Walmart" while the
    items-by-id lookup records "Walmart.com". Strict string equality
    therefore rejected genuine same-seller offers as
    ``live_seller_name_changed`` (observed live on item 599917754,
    2026-11-21). Two names that are both Walmart first-party forms are
    the same seller; every other pair still needs exact normalized
    equality, so a real offer swap to a different seller still fails.
    """

    first = _normalized_seller_name(original)
    second = _normalized_seller_name(refreshed)
    if not first or not second:
        return False
    if first == second:
        return True
    return first in WALMART_FIRST_PARTY_SELLERS and second in WALMART_FIRST_PARTY_SELLERS


async def live_extreme_offer_rejection_reason(candidate: Any, *, page_price: bool = False) -> str | None:
    """Refresh the exact Walmart item, offer and price right before public delivery.

    Invalid, changed or unavailable offers fail closed instead of forwarding a
    stale price. The canonical exact queue can create a new verified event.
    """
    identity = exact_offer_identity(candidate)
    if identity is None:
        return "original_identity_missing"
    provider = provider_registry.get("walmart")
    if provider is None:
        return "live_provider_unavailable"
    claim = _QueueClaim(
        item_id=identity.item_id,
        title=str(getattr(candidate, "title", "") or ""),
        product_url=str(getattr(candidate, "product_url", "") or ""),
        image_url=str(getattr(candidate, "image_url", "") or ""),
        apparent_current_cents=None,
        apparent_reference_cents=None,
        route_hint="extreme_pre_send_recheck",
        lease_token="",
        attempt_count=0,
    )
    try:
        refreshed, status, _error = await _fetch_exact_candidate_off_loop(
            provider, claim, timeout_seconds=6.0
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Walmart pre-send price check failed item=%s", identity.item_id)
        return "live_fetch_failed"
    if status != "verified" or refreshed is None:
        return "live_fetch_unverified"
    if exact_offer_identity(refreshed) != identity:
        return "live_offer_or_variant_changed"
    if not _same_seller_name(candidate, refreshed):
        return "live_seller_name_changed"
    if refreshed.can_add_to_cart is not True or is_unavailable_stock(refreshed.stock_status):
        return "live_not_buyable"
    attrs = getattr(refreshed, "variant_attributes", None) or {}
    if str(attrs.get("availableOnline") or "").lower() in {"no", "false", "0", "off"}:
        return "live_not_online"
    if refreshed.is_member_only or refreshed.is_checkout_price:
        return "live_checkout_restricted"
    old = page_observed_price(candidate) if page_price else _positive_number(getattr(candidate, "api_current_price", None))
    new = page_observed_price(refreshed) if page_price else _positive_number(getattr(refreshed, "api_current_price", None))
    if page_price and _positive_number(getattr(refreshed, "api_current_price", None)) is not None:
        return "page_offer_now_api_priced"
    if old is None or new is None:
        return "live_price_missing"
    # A $1.00 glitch ticking to $1.01 is noise, not a dead deal. Only a
    # move bigger than the tolerance kills the send here; the eligibility
    # re-check below still verifies the live price is an extreme lead.
    tolerance = max(
        LIVE_PRICE_TOLERANCE_CENTS / 100, old * LIVE_PRICE_TOLERANCE_RATIO
    )
    if abs(old - new) > tolerance:
        return "live_price_changed"
    if page_price and not eligible_page_price_extreme_candidate(refreshed):
        return "live_page_proof_invalid"
    if not page_price and not eligible_exact_extreme_candidate(refreshed):
        return "live_exact_proof_invalid"
    return None


async def live_underprice_rejection_reason(candidate: Any) -> str | None:
    """Refresh the exact offer right before an underprice lead DM.

    Mirrors ``live_extreme_offer_rejection_reason``: a lead DM tells a
    subscriber to go look *now*, and price errors die fast, so a stale
    snapshot is not enough. The refreshed offer must still be the same
    item/offer/seller, still buyable, within the same price tolerance,
    and must still satisfy the underprice detector against its own list
    price. Fetch failures are returned as their own reasons so the
    caller can hold the cursor and retry while the snapshot is fresh.
    """
    identity = exact_offer_identity(candidate)
    if identity is None:
        return "original_identity_missing"
    provider = provider_registry.get("walmart")
    if provider is None:
        return "live_provider_unavailable"
    claim = _QueueClaim(
        item_id=identity.item_id,
        title=str(getattr(candidate, "title", "") or ""),
        product_url=str(getattr(candidate, "product_url", "") or ""),
        image_url=str(getattr(candidate, "image_url", "") or ""),
        apparent_current_cents=None,
        apparent_reference_cents=None,
        route_hint="underprice_pre_send_recheck",
        lease_token="",
        attempt_count=0,
    )
    try:
        refreshed, status, _error = await _fetch_exact_candidate_off_loop(
            provider, claim, timeout_seconds=6.0
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Walmart underprice pre-send check failed item=%s", identity.item_id)
        return "live_fetch_failed"
    if status != "verified" or refreshed is None:
        return "live_fetch_unverified"
    if exact_offer_identity(refreshed) != identity:
        return "live_offer_or_variant_changed"
    if not _same_seller_name(candidate, refreshed):
        return "live_seller_name_changed"
    if refreshed.can_add_to_cart is not True or is_unavailable_stock(refreshed.stock_status):
        return "live_not_buyable"
    attrs = getattr(refreshed, "variant_attributes", None) or {}
    if str(attrs.get("availableOnline") or "").lower() in {"no", "false", "0", "off"}:
        return "live_not_online"
    if refreshed.is_member_only or refreshed.is_checkout_price:
        return "live_checkout_restricted"
    old = _positive_number(getattr(candidate, "api_current_price", None))
    new = _positive_number(getattr(refreshed, "api_current_price", None))
    if old is None or new is None:
        return "live_price_missing"
    tolerance = max(
        LIVE_PRICE_TOLERANCE_CENTS / 100, old * LIVE_PRICE_TOLERANCE_RATIO
    )
    if abs(old - new) > tolerance:
        return "live_price_changed"
    if underprice_lead_reference(refreshed) is None:
        return "live_underprice_proof_invalid"
    return None


def build_extreme_price_card(candidate: Any) -> Any | None:
    if not eligible_exact_extreme_candidate(candidate):
        return None
    current_price = float(candidate.api_current_price)
    reasons = ("high-value product type", "official Walmart exact-item price")
    lead = ScoutLead(candidate=candidate, score=70, reasons=reasons)
    card = build_scout_card(lead)
    card.url = candidate.direct_product_url or candidate.product_url
    card.embed.url = card.url
    card.embed.title = "Extreme exact-price find: " + str(candidate.title or "")[:155]
    # The private scout may show an untrusted raw MSRP. Replace it with exactly
    # what was verified, and never derive a discount percentage from it.
    price_text = (
        f"Exact offer price: **${current_price:,.2f}**. "
        "No verified was-price or discount percentage is claimed."
    )
    for index, field in enumerate(card.embed.fields):
        if str(field.name) == "💰 Scout price":
            card.embed.set_field_at(
                index,
                name="Walmart exact API price",
                value=price_text,
                inline=False,
            )
            break
    card.embed.add_field(
        name="Exact price, NOT a verified markdown",
        value=(
            "Walmart's official exact-item API returned $"
            + f"{current_price:,.2f}"
            + " for this seller/offer/variant. "
            + "No original price or discount is claimed. "
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


def build_page_price_extreme_card(candidate: Any) -> Any | None:
    """Build the opt-in extreme-lane card for a verified page-observed price.

    The card carries the page price as the observed price and labels it
    prominently as page-observed, not API-verified. No discount percentage
    is ever claimed and the card is never a verified markdown.
    """
    if not eligible_page_price_extreme_candidate(candidate):
        return None
    page_price = page_observed_price(candidate)
    if page_price is None:
        return None
    lead = ScoutLead(
        candidate=candidate,
        score=70,
        reasons=("page-observed price, not API-verified", "high-value product type"),
    )
    card = build_scout_card(lead)
    card.url = candidate.direct_product_url or candidate.product_url
    card.embed.url = card.url
    card.embed.title = "Page-observed price find: " + str(candidate.title or "")[:155]
    price_text = (
        f"Page-observed price: **${page_price:,.2f}**. Walmart's product page "
        "showed this price for this seller/offer/variant, but the official "
        "API returned no price for the item. That page price is not a verified "
        "was-price and not an API-verified price, so no discount percentage "
        "is claimed and this is not a verified markdown."
    )
    for index, field in enumerate(card.embed.fields):
        if str(field.name) == "💰 Scout price":
            card.embed.set_field_at(
                index,
                name="Walmart page-observed price",
                value=price_text,
                inline=False,
            )
            break
    card.embed.add_field(
        name="Page-observed price, NOT API-verified",
        value=(
            "Walmart's product page showed $"
            + f"{page_price:,.2f}"
            + " for this seller/offer/variant. The official exact-item API "
            "returned no usable price, so this lead is page evidence only. "
            "Recheck condition, local availability, shipping and checkout price."
        ),
        inline=False,
    )
    page_anomaly = listed_price_anomaly(candidate, current_price=page_price)
    if page_anomaly is not None:
        # The list price is the reason this over-$10 page price is notable
        # at all; state both numbers and that the list price is unverified,
        # exactly as the API-priced anomaly lane does. No discount is claimed.
        card.embed.add_field(
            name="Walmart list price (unverified)",
            value=(
                "Walmart lists this item at $"
                + f"{page_anomaly:,.2f}"
                + ". That list price is unverified — it is not a was-price "
                "and no discount percentage is claimed."
            ),
            inline=False,
        )
    card.embed.set_footer(text="Page-observed price lead; API price unverified; verify checkout")
    card.page_price_candidate = candidate
    # Durable lane marker: survives even if the candidate proof is stripped,
    # so the sender reports the right rejection reason for a forged card.
    card.page_price_lead = True
    # The observed price, so delivery dedupe and expiry tracking key on what
    # was actually shown. It is never presented as an API price.
    card.current_price = page_price
    card.should_alert = True
    return card


def valid_page_price_extreme_card(card: Any) -> bool:
    """Recheck the page-price provenance at the shared public-send boundary."""
    candidate = getattr(card, "page_price_candidate", None)
    if not eligible_page_price_extreme_candidate(candidate):
        return False
    try:
        page_price = page_observed_price(candidate)
        return (
            str(card.selected_offer_id or "") == str(candidate.selected_offer_id or "")
            and page_price is not None
            and abs(float(card.current_price) - page_price) < 0.01
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
        "AND exact_current_cents >= 1 "
        "AND verified_at >= ? AND snapshot_json <> '' "
        "ORDER BY verified_at DESC, item_id DESC LIMIT ?",
        (cutoff, limit),
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
                    "verified_reference", VERIFIED_PAGE_PRICE_STATUS,
                )
            )
            if same_proof:
                replay_candidate = _candidate_from_snapshot(snapshot)
                if str(_row_get(current, "status", 2)) == VERIFIED_PAGE_PRICE_STATUS:
                    card = build_page_price_extreme_card(replay_candidate)
                else:
                    card = build_extreme_price_card(replay_candidate)
            else:
                card = None
            if card is not None:
                issue = await live_extreme_offer_rejection_reason(
                    replay_candidate,
                    page_price=str(_row_get(current, "status", 2)) == VERIFIED_PAGE_PRICE_STATUS,
                )
                if issue is not None:
                    log.info("Walmart extreme replay rejected item=%s reason=%s", item_id, issue)
                    card = None
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


async def _deliver_extreme_card_to_dm_subscribers(
    bot: Any, db: Any, card: Any
) -> dict[str, int]:
    """DM an eligible extreme lead to subscribers who named the product.

    Personal alerts previously never fired for no-reference or page-price
    glitches: the bulk fanout only ingests verified_markdown rows, so extreme
    cards never reached the DM path at all, and the matcher rejected cards
    without a trusted was-price. The matcher now has an explicit-product-
    intent lead path (``dm_deal_matching``), and this pass runs it for
    extreme cards. Delivery reuses the bulk fanout's dedupe receipts, daily
    caps, and diversity gate, so a lead is DM'd at most once per subscriber.

    Best-effort by design: a DM failure never blocks the public lane or the
    cursor. The caller runs this after the public post succeeds — or, when
    no guild opted into the public lane, on its own: the DM opt-in is
    sufficient for DM delivery and the public opt-in gates only public posts.
    """

    totals = {
        "dm_preferences_checked": 0,
        "dm_matches": 0,
        "dm_sent": 0,
        "dm_skipped_duplicate": 0,
        "dm_skipped_daily_cap": 0,
        "dm_skipped_diversity": 0,
        "dm_disabled": 0,
        "dm_errors": 0,
    }
    try:
        preferences = await list_enabled_dm_deal_alert_preferences(db)
    except Exception:  # noqa: BLE001 - DMs must never break the public lane.
        log.exception("Extreme-price DM pass could not load preferences")
        return totals
    deal_key = card_deal_key(
        card, retailer=str(getattr(card, "retailer", None) or "walmart")
    )
    for preference in preferences:
        totals["dm_preferences_checked"] += 1
        decision = match_dm_deal(preference, card)
        record_user_dm_decision(
            preference.user_id, event_key=deal_key, decision=decision
        )
        if not decision.matched:
            continue
        totals["dm_matches"] += 1
        try:
            async with dm_delivery_lock(preference.user_id):
                if await dm_receipt_exists(
                    db, user_id=preference.user_id, deal_key=deal_key
                ):
                    totals["dm_skipped_duplicate"] += 1
                    record_user_dm_outcome(preference.user_id, "duplicate")
                    continue
                gate = await evaluate_dm_delivery_gate(
                    db, preference=preference, decision=decision
                )
                if not gate.allowed:
                    if gate.reason == "daily_cap":
                        totals["dm_skipped_daily_cap"] += 1
                    elif gate.reason == "diversity_cap":
                        totals["dm_skipped_diversity"] += 1
                    record_user_dm_outcome(preference.user_id, gate.reason)
                    continue
                user = bot.get_user(preference.user_id)
                if user is None:
                    user = await bot.fetch_user(preference.user_id)
                await user.send(
                    embed=legacy._personal_dm_embed(card, decision.reason)
                )
                await record_dm_delivery_receipt(
                    db,
                    user_id=preference.user_id,
                    deal_key=deal_key,
                    category_family=gate.category_family,
                )
                await clear_dm_delivery_failures(db, user_id=preference.user_id)
                record_user_dm_outcome(preference.user_id, "sent")
                totals["dm_sent"] += 1
        except discord.Forbidden as error:
            totals["dm_disabled"] += 1
            record_user_dm_outcome(preference.user_id, "disabled")
            await record_dm_delivery_failure(
                db,
                user_id=preference.user_id,
                error=f"Discord DMs closed: {error}",
                disable=True,
            )
        except discord.NotFound as error:
            totals["dm_disabled"] += 1
            record_user_dm_outcome(preference.user_id, "disabled")
            await record_dm_delivery_failure(
                db,
                user_id=preference.user_id,
                error=f"Discord user unavailable: {error}",
                disable=True,
            )
        except Exception as error:  # noqa: BLE001 - one subscriber cannot block others.
            totals["dm_errors"] += 1
            record_user_dm_outcome(preference.user_id, "error")
            await record_dm_delivery_failure(
                db,
                user_id=preference.user_id,
                error=f"{type(error).__name__}: {error}",
                disable=False,
            )
            log.exception(
                "Extreme-price DM fanout failed user=%s deal=%s",
                preference.user_id,
                deal_key,
            )
    return totals


async def fanout_recent_extreme_walmart_prices(bot: Any) -> dict[str, int]:
    """Post only opt-in, exact-verified, unusually cheap primary products.

    This uses its own cursor and existing public-send dedupe/reservations.
    It never advances or changes the verified-markdown/DM fanout cursor.

    Page-price rows (``verified_page_price``) ride the same opt-in lane with
    their own card: the official API returned no price, so the row carries
    no ``exact_current_cents`` value and is selected on status alone. The
    card labels the price as page-observed, not API-verified, and never
    claims a discount.

    The public lane is opt-in per guild, but that opt-in gates only public
    posting. When no guild opted in, eligible leads are still DM'd to
    subscribers who opted into DMs — a DM opt-in is sufficient on its own.
    """
    db = getattr(bot, "db", None)
    if db is None or _LOCK.locked():
        return {
            "examined": 0,
            "eligible": 0,
            "posted": 0,
            "dm_preferences_checked": 0,
            "dm_matches": 0,
            "dm_sent": 0,
            "dm_skipped_duplicate": 0,
            "dm_skipped_daily_cap": 0,
            "dm_skipped_diversity": 0,
            "dm_disabled": 0,
            "dm_errors": 0,
        }
    async with _LOCK:
        await ensure_extreme_tables(db)
        conn = db.require_conn()
        opted_cursor = await conn.execute(
            f"SELECT guild_id FROM {SETTINGS_TABLE} WHERE enabled = 1"
        )
        opted_ids = {str(_row_get(row, "guild_id", 0)) for row in await opted_cursor.fetchall()}
        # The public extreme lane is opt-in per guild, but it gates only
        # public posting — never DMs. A user who opted into DMs in a guild
        # that never opted into the public lane still gets the lead below,
        # so this function must NOT return early when no guild opted in.
        # Live-guild reconciliation still stays lazy: it only runs when an
        # eligible card exists (see the loop), not on every 20-second cycle.
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
            f"SELECT item_id, verified_at, snapshot_json, status FROM {QUEUE_TABLE} "
            "WHERE ("
            "  (status IN ('verified_no_reference', 'verified_under_threshold', 'verified_reference') "
            "   AND exact_current_cents >= 1) "
            f"  OR status = '{VERIFIED_PAGE_PRICE_STATUS}'"
            ") "
            "AND verified_at >= ? AND snapshot_json <> '' "
            "AND (verified_at, item_id) > (?, ?) "
            "ORDER BY verified_at ASC, item_id ASC LIMIT ?",
            (cutoff, *previous, BATCH_LIMIT),
        )
        rows = await cursor.fetchall()
        next_cursor = previous
        examined = eligible = 0
        posted = replayed
        dm_totals: Counter[str] = Counter()
        enabled: list[int] | None = (
            [
                int(guild.guild_id)
                for guild in replay_live.guilds
                if snowflake_text(guild.guild_id) in opted_ids
            ]
            if replay_live is not None else None
        )
        for row in rows:
            item_id, verified_at, snapshot, status = (
                str(_row_get(row, "item_id", 0)),
                str(_row_get(row, "verified_at", 1)),
                str(_row_get(row, "snapshot_json", 2)),
                str(_row_get(row, "status", 3)),
            )
            examined += 1
            candidate = _candidate_from_snapshot(snapshot)
            if status == VERIFIED_PAGE_PRICE_STATUS:
                card = build_page_price_extreme_card(candidate)
            else:
                card = build_extreme_price_card(candidate)
            if card is None and status != VERIFIED_PAGE_PRICE_STATUS:
                # Not a public extreme lead. It may still be a first-party
                # underprice lead (exact price far below Walmart's own
                # list price): those are DM-only speculative leads for
                # price-mistake subscribers and are NEVER posted publicly,
                # so there is no public opt-in gate on this path — the
                # subscriber's own DM opt-in is the gate, exactly as for
                # extreme leads when no guild opted into the public lane.
                lead_card = build_underprice_lead_card(candidate)
                if lead_card is not None:
                    issue = await live_underprice_rejection_reason(candidate)
                    if issue is not None:
                        log.info(
                            "Walmart underprice lead rejected item=%s reason=%s",
                            item_id, issue,
                        )
                        if issue in {
                            "live_fetch_unverified",
                            "live_fetch_failed",
                            "live_provider_unavailable",
                        }:
                            # Wait for the next cycle while snapshot
                            # freshness is valid.
                            break
                        next_cursor = (verified_at, item_id)
                        continue
                    dm_totals.update(
                        await _deliver_extreme_card_to_dm_subscribers(
                            bot, db, lead_card
                        )
                    )
                    next_cursor = (verified_at, item_id)
                    continue
            if card is not None:
                issue = await live_extreme_offer_rejection_reason(
                    candidate, page_price=status == VERIFIED_PAGE_PRICE_STATUS
                )
                if issue is not None:
                    log.info("Walmart extreme pre-send rejected item=%s reason=%s", item_id, issue)
                    if issue in {"live_fetch_unverified", "live_fetch_failed", "live_provider_unavailable"}:
                        # Wait for the next cycle while snapshot freshness is valid.
                        break
                    next_cursor = (verified_at, item_id)
                    continue
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
                if not enabled and opted_ids:
                    # Opted-in guilds exist but none are live in Discord's
                    # cache right now. Never let that cached empty result
                    # bypass the no-recipient guard or advance the shared
                    # cursor: the public post is retried next cycle.
                    log.warning(
                        "Walmart extreme-price eligible item has no currently "
                        "live opted-in destination; cursor retained item=%s "
                        "opted_in_count=%s",
                        item_id, len(opted_ids),
                    )
                    break
                if enabled:
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
                        # Public post succeeded; the card is now safe to match
                        # for personal DMs (matching badges the shared card title,
                        # so it must never run before the public post).
                        dm_totals.update(
                            await _deliver_extreme_card_to_dm_subscribers(
                                bot, db, card
                            )
                        )
                        next_cursor = (verified_at, item_id)
                        continue
                    break
                # No guild opted into the public lane: there is no public
                # post to wait for, so the DM opt-in alone is sufficient.
                # The lead is still DM'd only to subscribers with explicit
                # product/brand intent, under the same dedupe and daily
                # caps as the public-lane DM pass.
                dm_totals.update(
                    await _deliver_extreme_card_to_dm_subscribers(bot, db, card)
                )
                next_cursor = (verified_at, item_id)
                continue
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
        return {
            "examined": examined,
            "eligible": eligible,
            "posted": posted,
            "dm_preferences_checked": dm_totals["dm_preferences_checked"],
            "dm_matches": dm_totals["dm_matches"],
            "dm_sent": dm_totals["dm_sent"],
            "dm_skipped_duplicate": dm_totals["dm_skipped_duplicate"],
            "dm_skipped_daily_cap": dm_totals["dm_skipped_daily_cap"],
            "dm_skipped_diversity": dm_totals["dm_skipped_diversity"],
            "dm_disabled": dm_totals["dm_disabled"],
            "dm_errors": dm_totals["dm_errors"],
        }
