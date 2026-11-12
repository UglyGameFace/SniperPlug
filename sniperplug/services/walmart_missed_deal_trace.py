from __future__ import annotations

"""Read-only, bounded reconstruction of an exact Walmart item's delivery path.

Missing rows in an expiring queue or a bounded recent event sample are NOT
proof the source never returned an item. No API calls, DB schema mutations,
retries, reservations or Discord posts are performed here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.public_deal_posts import card_deal_key
from sniperplug.services.walmart_exact_price_enrichment import _candidate_item_id
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_from_snapshot,
)
from sniperplug.services import walmart_global_deal_fanout as fanout


RECENT_EVENTS_LIMIT = 200


@dataclass(frozen=True)
class WalmartDealTrace:
    item_id: str
    outcome: str
    detail: str
    queue_status: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    verified_at: str = ""
    next_attempt_at: str = ""
    last_error_category: str = ""
    event_seen_at: str = ""
    event_processed_at: str = ""
    post_status: str = ""
    posted_at: str = ""
    discovery_to_verification_seconds: int | None = None
    discovery_to_post_seconds: int | None = None
    verification_to_post_seconds: int | None = None
    sampled_events: int = 0
    query_error: str = ""


def _value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        pass
    try:
        return row[index]
    except (KeyError, TypeError, IndexError):
        return None


def _utc(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def _elapsed(first: str, second: str) -> int | None:
    start, end = _utc(first), _utc(second)
    if start is None or end is None or end < start:
        return None
    return int((end - start).total_seconds())


def _safe_error_class(raw: str) -> str:
    """Never echo raw API or DB exceptions into an admin embed."""
    msg = raw.lower()
    for term, label in (
        ("429", "rate_limited"), ("rate limit", "rate_limited"),
        ("403", "access_denied"), ("timeout", "timeout"),
        ("timed out", "timeout"), ("identity", "identity_incomplete"),
        ("seller", "seller_proof"), ("offer", "offer_proof"),
        ("no price", "price_missing"), ("price missing", "price_missing"),
        ("unavailable", "availability"), ("locked", "database_busy"),
    ):
        if term in msg:
            return label
    return "other_error" if raw else ""


async def _table_exists(conn: Any, name: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (name,),
    )
    return await cursor.fetchone() is not None


async def trace_walmart_item(
    db: Any, *, item_id: str, guild_id: int,
    event_limit: int = RECENT_EVENTS_LIMIT,
) -> WalmartDealTrace:
    """Trace one exact item, including retained queue and one guild's receipt.

    The event scan is capped and source snapshots are exact-ID compared. A
    missing event/receipt can mean retention, threshold suppression, muted
    categories or a processing error; only a stored decision can prove why.
    """
    item = str(item_id or "").strip()
    if not re.fullmatch(r"[0-9]{5,20}", item):
        return WalmartDealTrace(item, "invalid_item_id", "Use the numeric Walmart item ID from /ip/<id>.")
    if guild_id <= 0:
        return WalmartDealTrace(item, "invalid_guild", "A server context is required.")
    if db is None:
        return WalmartDealTrace(item, "unavailable", "Bot database is not configured.")
    try:
        conn = db.require_conn()
        if not await _table_exists(conn, QUEUE_TABLE):
            return WalmartDealTrace(item, "unavailable", "Exact-item queue is not initialized on this database.")

        cur = await conn.execute(
            f"""SELECT status, first_seen_at, last_seen_at, verified_at,
                       next_attempt_at, last_error
                FROM {QUEUE_TABLE} WHERE item_id = ? LIMIT 1""",
            (item,),
        )
        queue_row = await cur.fetchone()
        q_status = str(_value(queue_row, "status", 0) or "")
        first = str(_value(queue_row, "first_seen_at", 1) or "")
        last = str(_value(queue_row, "last_seen_at", 2) or "")
        verified = str(_value(queue_row, "verified_at", 3) or "")
        next_attempt = str(_value(queue_row, "next_attempt_at", 4) or "")
        err = _safe_error_class(str(_value(queue_row, "last_error", 5) or ""))

        event = None
        event_card = None
        searched = 0
        if await _table_exists(conn, fanout.EVENT_TABLE):
            cap = max(1, min(RECENT_EVENTS_LIMIT, int(event_limit)))
            cur = await conn.execute(
                f"""SELECT deal_key, snapshot_json, first_seen_at,
                           source_verified_at, processed_at, last_error
                    FROM {fanout.EVENT_TABLE}
                    ORDER BY first_seen_at DESC LIMIT ?""",
                (cap,),
            )
            event_rows = await cur.fetchall()
            searched = len(event_rows)
            for row in event_rows:
                candidate = _candidate_from_snapshot(_value(row, "snapshot_json", 1))
                if candidate is None or _candidate_item_id(candidate) != item:
                    continue
                event = row
                event_card = fanout._exact_card_for_candidate(candidate)
                break

        event_seen = str(_value(event, "first_seen_at", 2) or "")
        processed = str(_value(event, "processed_at", 4) or "")
        event_error = _safe_error_class(str(_value(event, "last_error", 5) or ""))
        receipt = None
        if event is not None and await _table_exists(conn, "guild_public_deal_posts"):
            keys = [str(_value(event, "deal_key", 0) or "")]
            if event_card is not None:
                public_key = str(
                    getattr(event_card, "public_post_key", None)
                    or card_deal_key(event_card, retailer="walmart")
                )
                if public_key and public_key not in keys:
                    keys.append(public_key)
            for key in keys[:2]:
                if not key:
                    continue
                cur = await conn.execute(
                    """SELECT status, posted_at FROM guild_public_deal_posts
                       WHERE CAST(guild_id AS TEXT) = ? AND retailer = 'walmart'
                         AND deal_key = ? LIMIT 1""",
                    (snowflake_text(guild_id), key),
                )
                receipt = await cur.fetchone()
                if receipt is not None:
                    break
        post_status = str(_value(receipt, "status", 0) or "")
        posted = str(_value(receipt, "posted_at", 1) or "")
        if post_status == "posted" and posted:
            outcome = "posted"
            detail = "Stored posted receipt exists for this guild."
        elif event is not None and not processed:
            outcome = "fanout_pending"
            detail = "Verified event exists; global fanout has not finished."
        elif event_error:
            outcome = "fanout_error"
            detail = "A verified event has a stored processing error."
        elif event is not None:
            outcome = "no_post_receipt"
            detail = "No posted receipt located. Delivery may be suppressed, deduplicated or outside retention; check guild decision audit."
        elif queue_row is not None:
            outcome = "queue_only"
            detail = f"Exact queue contains the item but no event among the last {searched} inspected. Not proof the item was never posted."
        else:
            outcome = "not_in_retained_sample"
            detail = f"No queue row or matching event among the last {searched} inspected. Source visibility remains unverified."
        return WalmartDealTrace(
            item_id=item,
            outcome=outcome,
            detail=detail,
            queue_status=q_status,
            first_seen_at=first,
            last_seen_at=last,
            verified_at=verified,
            next_attempt_at=next_attempt,
            last_error_category=event_error or err,
            event_seen_at=event_seen,
            event_processed_at=processed,
            post_status=post_status,
            posted_at=posted,
            discovery_to_verification_seconds=_elapsed(first, verified),
            discovery_to_post_seconds=_elapsed(first, posted),
            verification_to_post_seconds=_elapsed(verified, posted),
            sampled_events=searched,
        )
    except Exception as exc:
        return WalmartDealTrace(
            item, "query_error",
            "Read-only trace could not complete; no absence claim is justified.",
            query_error=type(exc).__name__,
        )
