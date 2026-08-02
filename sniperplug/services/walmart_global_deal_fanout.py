from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.autoscan_live_guild_reconciliation import (
    list_live_public_alert_guilds,
)
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.dm_deal_alerts import (
    clear_dm_delivery_failures,
    dm_alerts_sent_today,
    dm_receipt_exists,
    list_enabled_dm_deal_alert_preferences,
    match_dm_deal,
    record_dm_delivery_failure,
    record_dm_receipt,
)
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.public_deal_posts import (
    card_deal_key,
    maybe_post_public_deal_cards,
)
from sniperplug.services.walmart_exact_public_lane import (
    normalize_exact_verified_walmart_cards,
)
from sniperplug.services.walmart_exact_verification_queue import (
    load_recent_verified_queue_candidates,
)


EVENT_TABLE = "walmart_global_exact_deal_events"
FANOUT_LOOKBACK_MINUTES = 120
FANOUT_CANDIDATE_LIMIT = 100
FANOUT_EVENT_LIMIT = 20
_FANOUT_LOCK = asyncio.Lock()
log = logging.getLogger("sniperplug.autoscan.fanout")


@dataclass(frozen=True)
class GlobalDealFanoutResult:
    candidates_loaded: int = 0
    exact_cards: int = 0
    new_events: int = 0
    events_processed: int = 0
    guilds_checked: int = 0
    public_posts: int = 0
    public_errors: int = 0
    dm_preferences_checked: int = 0
    dm_matches: int = 0
    dm_sent: int = 0
    dm_disabled: int = 0
    dm_errors: int = 0

    def summary_line(self) -> str:
        return (
            "global exact-deal fanout: "
            f"loaded **{self.candidates_loaded}** • exact cards **{self.exact_cards}** • "
            f"new events **{self.new_events}** • processed **{self.events_processed}** • "
            f"guild posts **{self.public_posts}** • DM sent **{self.dm_sent}** • "
            f"errors public/DM **{self.public_errors}/{self.dm_errors}**"
        )


async def ensure_global_deal_event_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
            deal_key TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_attempt_at TEXT,
            processed_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{EVENT_TABLE}_pending "
        f"ON {EVENT_TABLE} (processed_at, first_seen_at)"
    )
    await conn.commit()


async def fanout_recent_exact_walmart_deals(
    bot: Any,
    *,
    event_limit: int = FANOUT_EVENT_LIMIT,
) -> GlobalDealFanoutResult:
    """Fan out newly exact-verified Walmart markdowns once.

    Public delivery still goes through every guild's threshold, category,
    channel, exact-proof, and duplicate gates. Personal DMs use their own
    opt-in filters and durable per-user receipts. A failed event remains pending;
    already successful guild/DM sends are protected by their normal dedupe rows
    when the event is retried.
    """

    db = getattr(bot, "db", None)
    if db is None:
        return GlobalDealFanoutResult()

    if _FANOUT_LOCK.locked():
        return GlobalDealFanoutResult()

    async with _FANOUT_LOCK:
        await ensure_global_deal_event_table(db)
        candidates = await load_recent_verified_queue_candidates(
            db,
            limit=FANOUT_CANDIDATE_LIMIT,
            max_age_minutes=FANOUT_LOOKBACK_MINUTES,
        )
        if not candidates:
            return GlobalDealFanoutResult()

        aggregate = ProviderScanResult(
            provider_key="walmart",
            candidates=tuple(candidates),
            page=1,
            page_size=len(candidates),
            start_index=1,
            has_next_page=False,
        )
        cards = deal_scanner.build_walmart_cards(
            aggregate,
            min_discount=1,
            alerts_only=False,
        )
        normalize_exact_verified_walmart_cards(cards, min_discount=1)
        cards = deal_scanner.dedupe_cards(cards) if hasattr(deal_scanner, "dedupe_cards") else cards

        pending_cards: list[tuple[str, Any]] = []
        new_events = 0
        for card in cards:
            retailer = str(getattr(card, "retailer", None) or "walmart")
            deal_key = card_deal_key(card, retailer=retailer)
            created = await _ensure_pending_event(db, deal_key)
            new_events += int(created)
            if await _event_is_pending(db, deal_key):
                pending_cards.append((deal_key, card))
            if len(pending_cards) >= max(1, int(event_limit)):
                break

        if not pending_cards:
            return GlobalDealFanoutResult(
                candidates_loaded=len(candidates),
                exact_cards=len(cards),
                new_events=new_events,
            )

        load_result = await list_live_public_alert_guilds(db, bot)
        guilds = list(load_result.guilds)
        preferences = await list_enabled_dm_deal_alert_preferences(db)

        totals = {
            "events_processed": 0,
            "guilds_checked": 0,
            "public_posts": 0,
            "public_errors": 0,
            "dm_preferences_checked": 0,
            "dm_matches": 0,
            "dm_sent": 0,
            "dm_disabled": 0,
            "dm_errors": 0,
        }

        for deal_key, card in pending_cards:
            event_errors: list[str] = []
            await _mark_event_attempt(db, deal_key)

            for guild in guilds:
                guild_id = int(guild.guild_id)
                totals["guilds_checked"] += 1
                try:
                    threshold = await get_starting_deal_percent(db, guild_id)
                    public_result = await maybe_post_public_deal_cards(
                        bot=bot,
                        guild_id=guild_id,
                        cards=[card],
                        source_label="global_catalog_autoscan:exact_verified",
                        fallback_retailer="walmart",
                        min_public_discount=int(threshold),
                    )
                    totals["public_posts"] += int(public_result.posted)
                    if public_result.errors:
                        totals["public_errors"] += len(public_result.errors)
                        event_errors.extend(public_result.errors)
                except Exception as error:  # noqa: BLE001 - one guild cannot block global fanout.
                    totals["public_errors"] += 1
                    event_errors.append(
                        f"guild {guild_id}: {type(error).__name__}: {error}"
                    )
                    log.exception(
                        "Global Walmart public fanout failed guild=%s deal=%s",
                        guild_id,
                        deal_key,
                    )

            for preference in preferences:
                totals["dm_preferences_checked"] += 1
                decision = match_dm_deal(preference, card)
                if not decision.matched:
                    continue
                totals["dm_matches"] += 1
                try:
                    if await dm_receipt_exists(
                        db,
                        user_id=preference.user_id,
                        deal_key=deal_key,
                    ):
                        continue
                    sent_today = await dm_alerts_sent_today(db, preference.user_id)
                    if sent_today >= preference.max_alerts_per_day:
                        continue
                    user = bot.get_user(preference.user_id)
                    if user is None:
                        user = await bot.fetch_user(preference.user_id)
                    embed = _personal_dm_embed(card, decision.reason)
                    await user.send(embed=embed)
                    await record_dm_receipt(
                        db,
                        user_id=preference.user_id,
                        deal_key=deal_key,
                    )
                    await clear_dm_delivery_failures(db, user_id=preference.user_id)
                    totals["dm_sent"] += 1
                except discord.Forbidden as error:
                    totals["dm_disabled"] += 1
                    await record_dm_delivery_failure(
                        db,
                        user_id=preference.user_id,
                        error=f"Discord DMs closed: {error}",
                        disable=True,
                    )
                except discord.NotFound as error:
                    totals["dm_disabled"] += 1
                    await record_dm_delivery_failure(
                        db,
                        user_id=preference.user_id,
                        error=f"Discord user unavailable: {error}",
                        disable=True,
                    )
                except Exception as error:  # noqa: BLE001 - one subscriber cannot block the stream.
                    totals["dm_errors"] += 1
                    event_errors.append(
                        f"DM {preference.user_id}: {type(error).__name__}: {error}"
                    )
                    await record_dm_delivery_failure(
                        db,
                        user_id=preference.user_id,
                        error=f"{type(error).__name__}: {error}",
                        disable=False,
                    )
                    log.exception(
                        "Global Walmart DM fanout failed user=%s deal=%s",
                        preference.user_id,
                        deal_key,
                    )

            # Public HTTP/database errors are retried. Closed-DM failures are
            # terminal for that subscriber because their preference is disabled.
            if event_errors:
                await _record_event_error(db, deal_key, event_errors)
            else:
                await _mark_event_processed(db, deal_key)
                totals["events_processed"] += 1

        return GlobalDealFanoutResult(
            candidates_loaded=len(candidates),
            exact_cards=len(cards),
            new_events=new_events,
            **totals,
        )


async def _ensure_pending_event(db: Any, deal_key: str) -> bool:
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT 1 FROM {EVENT_TABLE} WHERE deal_key = ? LIMIT 1",
        (deal_key,),
    )
    existed = await cursor.fetchone() is not None
    if existed:
        return False
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        INSERT INTO {EVENT_TABLE} (
            deal_key, first_seen_at, attempt_count, last_error
        ) VALUES (?, ?, 0, '')
        ON CONFLICT(deal_key) DO NOTHING
        """,
        (deal_key, now),
    )
    await conn.commit()
    return True


async def _event_is_pending(db: Any, deal_key: str) -> bool:
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT processed_at FROM {EVENT_TABLE} WHERE deal_key = ?",
        (deal_key,),
    )
    row = await cursor.fetchone()
    return row is not None and not str(_row_get(row, "processed_at", 0) or "").strip()


async def _mark_event_attempt(db: Any, deal_key: str) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        UPDATE {EVENT_TABLE}
        SET last_attempt_at = ?, attempt_count = attempt_count + 1
        WHERE deal_key = ?
        """,
        (now, deal_key),
    )
    await conn.commit()


async def _mark_event_processed(db: Any, deal_key: str) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        UPDATE {EVENT_TABLE}
        SET processed_at = ?, last_error = ''
        WHERE deal_key = ?
        """,
        (now, deal_key),
    )
    await conn.commit()


async def _record_event_error(db: Any, deal_key: str, errors: list[str]) -> None:
    conn = db.require_conn()
    text = " | ".join(" ".join(str(error).split()) for error in errors if error)
    if len(text) > 1000:
        text = text[:999] + "…"
    await conn.execute(
        f"UPDATE {EVENT_TABLE} SET last_error = ? WHERE deal_key = ?",
        (text, deal_key),
    )
    await conn.commit()


def _personal_dm_embed(card: Any, reason: str) -> discord.Embed:
    source = getattr(card, "embed", None)
    if source is None:
        embed = discord.Embed(
            title=str(getattr(card, "label", "Walmart deal")),
            url=str(getattr(card, "url", "") or "") or None,
            color=discord.Color.green(),
        )
    else:
        embed = discord.Embed.from_dict(source.to_dict())
    value = (
        f"{reason}\n"
        "This was sent by your opt-in `/dm_deals` filter. Prices can change; "
        "open the exact product offer before buying."
    )
    if len(embed.fields) < 25:
        embed.add_field(name="🔔 Your smart alert", value=value[:1024], inline=False)
    else:
        embed.set_footer(text="Matched your /dm_deals smart filters • Recheck before buying")
    return sanitize_embed(embed)


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)
