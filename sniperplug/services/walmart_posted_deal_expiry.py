"""Retire Walmart Discord posts whose offer has ended.

Lifecycle: every public Walmart post (markdown lane and opt-in extreme lane,
both of which send through ``maybe_post_public_deal_cards``) is recorded here
with its channel, message, item ID and the price it was posted at. A bounded
sweep then looks for *authoritative* evidence that the posted offer is gone:

1. Free: the exact worker already re-verified the item after it was posted and
   the queue row is ``not_buyable`` or the exact price rose materially.
2. Paced: at most one batched Product Lookup (<= 20 item IDs) per sweep for
   posts not checked in ``RECHECK_AFTER``; an item that is out of stock, not
   available online, or priced materially higher counts as ended.

On evidence the original message is edited in place: title prefixed
"EXPIRED", grey colour, a "Deal ended" field with the reason and check time,
and the buttons removed. No new message, no ping. Absence from a lookup
response is never treated as expiry (Walmart omits items for many reasons);
such posts simply keep their original timestamp, which states their age.
Posts older than ``TRACK_FOR`` stop being tracked; rows are pruned after
``RETAIN_FOR`` in bounded batches.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from sniperplug.services.public_deal_quality import walmart_item_id_from_url
from sniperplug.services.stock_status import is_unavailable_stock

log = logging.getLogger("sniperplug.autoscan.expiry")

TABLE = "walmart_posted_deal_messages"
SWEEP_INTERVAL_SECONDS = 10 * 60
RECHECK_AFTER = timedelta(hours=2)
TRACK_FOR = timedelta(days=7)
RETAIN_FOR = timedelta(days=30)
QUEUE_CHECK_LIMIT = 100
API_CHECK_LIMIT = 20
PRUNE_LIMIT = 200
MAX_EDIT_ATTEMPTS = 3
# A price must rise by both of these before the posted deal counts as ended.
PRICE_RISE_RATIO = 1.05
PRICE_RISE_MIN_CENTS = 100

_SCHEMA_ATTR = "_sniperplug_posted_deal_expiry_schema"
_SWEEP_ATTR = "_sniperplug_posted_deal_expiry_last_sweep"


@dataclass(frozen=True)
class ExpirySweepResult:
    ran: bool = False
    tracked_checked: int = 0
    api_calls: int = 0
    expired: int = 0
    edit_failures: int = 0
    untracked: int = 0
    pruned: int = 0

    def summary_line(self) -> str:
        return (
            f"Posted-deal expiry sweep checked={self.tracked_checked} "
            f"api_calls={self.api_calls} expired={self.expired} "
            f"edit_failures={self.edit_failures} untracked={self.untracked} "
            f"pruned={self.pruned}"
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return row[index]
    except (KeyError, IndexError, TypeError):
        return None


def _cents(value: Any) -> int | None:
    try:
        cents = int(round(float(value) * 100))
    except (TypeError, ValueError, OverflowError):
        return None
    return cents if cents > 0 else None


async def ensure_posted_deal_tables(db: Any) -> None:
    if getattr(db, _SCHEMA_ATTR, False):
        return
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            guild_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            product_key TEXT NOT NULL DEFAULT '',
            deal_key TEXT NOT NULL DEFAULT '',
            alert_key TEXT NOT NULL DEFAULT '',
            posted_cents INTEGER NOT NULL,
            lane TEXT NOT NULL DEFAULT '',
            posted_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            expired_reason TEXT NOT NULL DEFAULT '',
            expired_at TEXT,
            edit_attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, message_id)
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_state_checked "
        f"ON {TABLE} (state, last_checked_at)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_state_posted "
        f"ON {TABLE} (state, posted_at)"
    )
    await conn.commit()
    try:
        setattr(db, _SCHEMA_ATTR, True)
    except Exception:  # noqa: BLE001 - schema creation is idempotent anyway.
        pass


async def record_posted_walmart_message(
    db: Any,
    *,
    guild_id: int,
    channel_id: Any,
    message_id: Any,
    card: Any,
    product_key: str,
    lane: str,
    deal_key: str = "",
    alert_key: str = "",
) -> bool:
    """Remember a sent Walmart post so it can be retired when the deal ends."""

    item_id = walmart_item_id_from_url(
        getattr(card, "direct_product_url", None) or getattr(card, "url", None)
    )
    posted_cents = _cents(getattr(card, "current_price", None))
    if not item_id or posted_cents is None or not message_id or not channel_id:
        return False
    await ensure_posted_deal_tables(db)
    now = _now().isoformat()
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {TABLE} (guild_id, message_id, channel_id, item_id, product_key,
            deal_key, alert_key, posted_cents, lane, posted_at, last_checked_at, state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ON CONFLICT(guild_id, message_id) DO NOTHING
        """,
        (
            str(int(guild_id)), str(message_id), str(channel_id), str(item_id),
            str(product_key or ""), str(deal_key or ""), str(alert_key or ""),
            posted_cents, str(lane or "")[:40], now, now,
        ),
    )
    await conn.commit()
    return True


def _queue_evidence(row: Any) -> tuple[str, int | None] | None:
    status = str(_row(row, "status", 4) or "")
    posted = int(_row(row, "posted_cents", 3) or 0)
    current = _row(row, "exact_current_cents", 5)
    try:
        current = int(current) if current is not None else None
    except (TypeError, ValueError):
        current = None
    if status == "not_buyable":
        return "Walmart no longer shows this offer as buyable online", current
    if current and _price_rose(posted, current):
        return f"Walmart's price is now ${current / 100:,.2f}", current
    return None


def _price_rose(posted_cents: int, current_cents: int) -> bool:
    return (
        current_cents >= posted_cents * PRICE_RISE_RATIO
        and current_cents - posted_cents >= PRICE_RISE_MIN_CENTS
    )


def _lookup_evidence(item: dict, posted_cents: int) -> tuple[str, int | None] | None:
    stock = str(item.get("stock") or "").strip().lower()
    online = item.get("availableOnline")
    if isinstance(online, str):
        online = online.strip().lower() not in {"false", "no", "0", "off"}
    current = _cents(item.get("salePrice"))
    if is_unavailable_stock(stock):
        return "Walmart now shows this item as out of stock", current
    if online is False:
        return "Walmart no longer offers this item online", current
    if current and _price_rose(posted_cents, current):
        return f"Walmart's price is now ${current / 100:,.2f}", current
    return None


async def sweep_posted_walmart_deals(
    bot: Any,
    *,
    provider: Any | None,
    force: bool = False,
) -> ExpirySweepResult:
    """Run one bounded expiry sweep at most every ``SWEEP_INTERVAL_SECONDS``."""

    db = getattr(bot, "db", None)
    if db is None:
        return ExpirySweepResult()
    now_mono = time.monotonic()
    last = getattr(bot, _SWEEP_ATTR, None)
    if not force and last is not None and now_mono - last < SWEEP_INTERVAL_SECONDS:
        return ExpirySweepResult()
    try:
        setattr(bot, _SWEEP_ATTR, now_mono)
    except Exception:  # noqa: BLE001
        pass

    await ensure_posted_deal_tables(db)
    conn = db.require_conn()
    now = _now()
    now_iso = now.isoformat()
    track_cutoff = (now - TRACK_FOR).isoformat()

    # Stop tracking posts that are too old to be presented as current anyway.
    cursor = await conn.execute(
        f"""
        UPDATE {TABLE} SET state = 'untracked'
        WHERE rowid IN (
            SELECT rowid FROM {TABLE}
            WHERE state = 'active' AND posted_at < ?
            LIMIT ?
        )
        """,
        (track_cutoff, PRUNE_LIMIT),
    )
    untracked = max(0, int(getattr(cursor, "rowcount", 0) or 0))
    cursor = await conn.execute(
        f"""
        DELETE FROM {TABLE}
        WHERE rowid IN (
            SELECT rowid FROM {TABLE}
            WHERE state <> 'active' AND posted_at < ?
            LIMIT ?
        )
        """,
        ((now - RETAIN_FOR).isoformat(), PRUNE_LIMIT),
    )
    pruned = max(0, int(getattr(cursor, "rowcount", 0) or 0))
    await conn.commit()

    pending: dict[tuple[str, str], tuple[Any, str]] = {}

    # 1) Free evidence: the exact worker re-verified the item after posting.
    from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE

    cursor = await conn.execute(
        f"""
        SELECT p.guild_id, p.message_id, p.channel_id, p.posted_cents,
               q.status, q.exact_current_cents, p.item_id, p.product_key,
               p.edit_attempts, p.deal_key, p.alert_key
        FROM {TABLE} AS p
        JOIN {QUEUE_TABLE} AS q ON q.item_id = p.item_id
        WHERE p.state = 'active'
          AND p.posted_at >= ?
          AND q.verified_at IS NOT NULL
          AND q.verified_at > p.posted_at
        ORDER BY p.posted_at DESC
        LIMIT ?
        """,
        (track_cutoff, QUEUE_CHECK_LIMIT),
    )
    queue_rows = await cursor.fetchall()
    for row in queue_rows:
        evidence = _queue_evidence(row)
        if evidence is not None:
            key = (str(_row(row, "guild_id", 0)), str(_row(row, "message_id", 1)))
            pending[key] = (row, evidence[0])

    # 2) Paced evidence: one batched Product Lookup for the stalest posts.
    api_calls = 0
    checked = len(queue_rows)
    cursor = await conn.execute(
        f"""
        SELECT guild_id, message_id, channel_id, posted_cents, item_id,
               product_key, edit_attempts, deal_key, alert_key
        FROM {TABLE} INDEXED BY idx_{TABLE}_state_checked
        WHERE state = 'active' AND last_checked_at <= ? AND posted_at >= ?
        ORDER BY last_checked_at ASC
        LIMIT ?
        """,
        ((now - RECHECK_AFTER).isoformat(), track_cutoff, API_CHECK_LIMIT * 3),
    )
    stale_rows = await cursor.fetchall()
    item_ids: list[str] = []
    for row in stale_rows:
        item_id = str(_row(row, "item_id", 4) or "")
        if item_id and item_id not in item_ids and len(item_ids) < API_CHECK_LIMIT:
            item_ids.append(item_id)
    if item_ids and provider is not None:
        from sniperplug.services.walmart_exact_batch_prefetch import fetch_exact_lookup_items

        items, api_calls, failed_calls = await fetch_exact_lookup_items(provider, item_ids)
        checked_ids = set(item_ids) if not failed_calls else set(items)
        for row in stale_rows:
            item_id = str(_row(row, "item_id", 4) or "")
            item = items.get(item_id)
            if item is None:
                continue
            evidence = _lookup_evidence(item, int(_row(row, "posted_cents", 3) or 0))
            if evidence is not None:
                key = (str(_row(row, "guild_id", 0)), str(_row(row, "message_id", 1)))
                pending.setdefault(key, (row, evidence[0]))
        if checked_ids:
            placeholders = ",".join("?" for _ in checked_ids)
            await conn.execute(
                f"UPDATE {TABLE} SET last_checked_at = ? "
                f"WHERE state = 'active' AND item_id IN ({placeholders})",
                (now_iso, *sorted(checked_ids)),
            )
            await conn.commit()
        checked += len(checked_ids)

    expired = failures = 0
    expired_offers: set[tuple[str, int]] = set()
    for (guild_id, message_id), (row, reason) in pending.items():
        outcome = await _retire_message(
            bot,
            channel_id=str(_row(row, "channel_id", 2) or ""),
            message_id=message_id,
            posted_cents=int(_row(row, "posted_cents", 3) or 0),
            reason=reason,
            checked_at=now,
        )
        attempts = int(_row(row, "edit_attempts", 8) or 0) + (0 if outcome == "edited" else 1)
        if outcome == "edited":
            state = "expired"
            expired += 1
        elif outcome == "gone" or attempts >= MAX_EDIT_ATTEMPTS:
            state = "gone"
            failures += 1
        else:
            state = "active"
            failures += 1
        await conn.execute(
            f"""
            UPDATE {TABLE}
            SET state = ?, expired_reason = ?, expired_at = ?, edit_attempts = ?
            WHERE guild_id = ? AND message_id = ?
            """,
            (state, reason[:300], now_iso if state != "active" else None, attempts,
             guild_id, message_id),
        )
        product_key = str(_row(row, "product_key", 7) or "")
        if state == "expired":
            expired_offers.add(
                (str(_row(row, "item_id", 6) or ""), int(_row(row, "posted_cents", 3) or 0))
            )
            await _release_delivery_guards(
                conn,
                guild_id=guild_id,
                product_key=product_key,
                deal_key=str(_row(row, "deal_key", 9) or ""),
                alert_key=str(_row(row, "alert_key", 10) or ""),
            )
        if state == "expired" and product_key:
            try:
                await conn.execute(
                    "UPDATE guild_active_deal_cache SET status = 'expired' "
                    "WHERE guild_id = ? AND active_key = ?",
                    (int(guild_id), product_key),
                )
            except Exception:  # noqa: BLE001 - cache table is optional.
                pass
    if expired_offers:
        await _rearm_markdown_events(conn, expired_offers)
    if pending:
        await conn.commit()

    result = ExpirySweepResult(
        ran=True,
        tracked_checked=checked,
        api_calls=api_calls,
        expired=expired,
        edit_failures=failures,
        untracked=untracked,
        pruned=pruned,
    )
    if expired or failures or api_calls:
        log.info(result.summary_line())
    return result


async def _release_delivery_guards(
    conn: Any,
    *,
    guild_id: str,
    product_key: str,
    deal_key: str,
    alert_key: str,
) -> None:
    """Let a retired deal post again in this server if it genuinely returns.

    The per-server reservation (price-keyed) and the alert dedupe row
    (suppresses any price >= the last posted price for days) would otherwise
    keep a restocked or re-dropped offer at the same price from ever posting
    again, leaving only the EXPIRED message. A new post still requires a fresh
    exact verification at the deal price, so this cannot replay the old one.
    """

    try:
        gid = int(guild_id)
    except (TypeError, ValueError):
        return
    try:
        if deal_key:
            await conn.execute(
                "DELETE FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ?",
                (gid, deal_key),
            )
        if product_key and alert_key:
            await conn.execute(
                "DELETE FROM alert_dedupe WHERE guild_id = ? AND retailer = 'walmart' "
                "AND product_key = ? AND alert_key = ?",
                (gid, product_key, alert_key),
            )
    except Exception as error:  # noqa: BLE001 - tables are created elsewhere.
        log.info("Posted-deal guard release skipped guild=%s error=%s", guild_id, str(error)[:200])


async def _rearm_markdown_events(conn: Any, offers: set[tuple[str, int]]) -> None:
    """Forget processed markdown events for retired offers.

    Markdown fanout events are keyed by item and price and created only when
    the exact worker verifies a new markdown. Leaving the processed event in
    place meant a deal that returned at the same price never produced a new
    event. Removing it lets the *next fresh verification* at that price create
    one; servers that did not retire their post keep their own delivery
    guards, so they are not sent a duplicate.
    """

    from sniperplug.services.walmart_global_deal_fanout import EVENT_TABLE

    for item_id, posted_cents in offers:
        if not item_id.isdigit() or posted_cents <= 0:
            continue
        prefix = f"walmart:{item_id}:price:{posted_cents / 100:.2f}:"
        try:
            await conn.execute(
                f"DELETE FROM {EVENT_TABLE} WHERE processed_at IS NOT NULL "
                "AND substr(deal_key, 1, ?) = ?",
                (len(prefix), prefix),
            )
        except Exception as error:  # noqa: BLE001
            log.info("Posted-deal event re-arm skipped item=%s error=%s", item_id, str(error)[:200])


EMBED_MAX_FIELDS = 25
EMBED_MAX_CHARS = 6000


def _expired_title(title: str) -> str:
    if title.startswith("⛔ EXPIRED"):
        return title[:256]
    return ("⛔ EXPIRED — " + title)[:256]


def _expired_embed(embed: Any, ended_value: str) -> Any:
    """Mark an embed expired while staying inside Discord's embed limits."""

    embed.title = _expired_title(str(getattr(embed, "title", "") or ""))
    embed.colour = discord.Colour.dark_grey()
    # Make room for the new first field (max 25 fields).
    while len(list(getattr(embed, "fields", []) or [])) >= EMBED_MAX_FIELDS:
        embed.remove_field(len(embed.fields) - 1)
    embed.insert_field_at(0, name="⛔ Deal ended", value=ended_value, inline=False)
    # Keep the total under Discord's 6,000-character limit by trimming the
    # description, then dropping trailing fields (never the "Deal ended" one).
    try:
        overflow = len(embed) - EMBED_MAX_CHARS
    except Exception:  # noqa: BLE001 - unknown embed type; let Discord decide.
        overflow = 0
    if overflow > 0 and embed.description:
        keep = max(0, len(embed.description) - overflow - 1)
        embed.description = embed.description[:keep] + ("…" if keep else "")
        overflow = len(embed) - EMBED_MAX_CHARS
    while overflow > 0 and len(embed.fields) > 1:
        embed.remove_field(len(embed.fields) - 1)
        overflow = len(embed) - EMBED_MAX_CHARS
    return embed


async def _retire_message(
    bot: Any,
    *,
    channel_id: str,
    message_id: str,
    posted_cents: int,
    reason: str,
    checked_at: datetime,
) -> str:
    """Edit one post in place. Returns ``edited``, ``gone`` or ``retry``."""

    try:
        channel = None
        getter = getattr(bot, "get_channel", None)
        if callable(getter):
            channel = getter(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
        message = await channel.fetch_message(int(message_id))
        embeds = list(getattr(message, "embeds", None) or [])
        stamp = int(checked_at.timestamp())
        ended_value = (
            f"{reason}. It was posted at ${posted_cents / 100:,.2f}. "
            f"Checked <t:{stamp}:R>."
        )[:1024]
        embed = _expired_embed(
            embeds[0].copy() if embeds else discord.Embed(title="Walmart deal"),
            ended_value,
        )
        try:
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden):
            raise
        except discord.HTTPException as error:
            if int(getattr(error, "status", 0) or 0) != 400:
                raise
            # Discord rejected the edited embed (size/field limits). Retire the
            # post with a minimal embed rather than leaving it looking live.
            original = embeds[0] if embeds else None
            minimal = discord.Embed(
                title=_expired_title(str(getattr(original, "title", "") or "Walmart deal")),
                url=getattr(original, "url", None) or None,
                colour=discord.Colour.dark_grey(),
            )
            minimal.add_field(name="⛔ Deal ended", value=ended_value, inline=False)
            await message.edit(embed=minimal, view=None)
        return "edited"
    except (discord.NotFound, discord.Forbidden):
        return "gone"
    except LookupError:
        return "gone"
    except Exception as error:  # noqa: BLE001 - transient; retried next sweep.
        log.info(
            "Posted-deal expiry edit failed channel=%s message=%s error=%s",
            channel_id, message_id, str(error)[:200],
        )
        return "retry"
