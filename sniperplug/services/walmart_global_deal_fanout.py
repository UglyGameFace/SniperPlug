from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    record_dm_delivery_failure,
    record_dm_receipt,
)
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.public_deal_posts import (
    card_deal_key,
    maybe_post_public_deal_cards,
)
from sniperplug.services.public_deal_quality import is_public_deal_candidate
from sniperplug.services.walmart_exact_public_lane import (
    normalize_exact_verified_walmart_cards,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_from_snapshot,
)


EVENT_TABLE = "walmart_global_exact_deal_events"
STATE_TABLE = "walmart_global_exact_deal_fanout_state"
STATE_KEY = "walmart"
FANOUT_INGEST_LIMIT = 150
FANOUT_EVENT_LIMIT = 20
EVENT_RETENTION_DAYS = 30
EVENT_LEASE_SECONDS = 30 * 60
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


@dataclass(frozen=True)
class _IngestResult:
    loaded: int = 0
    exact_cards: int = 0
    new_events: int = 0


@dataclass(frozen=True)
class _ClaimedEvent:
    deal_key: str
    claim_token: str
    card: Any


async def ensure_global_deal_event_tables(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
            deal_key TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            source_verified_at TEXT NOT NULL,
            last_attempt_at TEXT,
            processed_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            claim_token TEXT NOT NULL DEFAULT '',
            lease_until TEXT
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{EVENT_TABLE}_pending "
        f"ON {EVENT_TABLE} (processed_at, lease_until, first_seen_at)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
            state_key TEXT PRIMARY KEY,
            last_verified_at TEXT NOT NULL DEFAULT '',
            last_item_id TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        INSERT INTO {STATE_TABLE} (
            state_key, last_verified_at, last_item_id, updated_at
        ) VALUES (?, '', '', ?)
        ON CONFLICT(state_key) DO NOTHING
        """,
        (STATE_KEY, now),
    )
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS)
    ).isoformat()
    await conn.execute(
        f"""
        DELETE FROM {EVENT_TABLE}
        WHERE processed_at IS NOT NULL AND first_seen_at < ?
        """,
        (cutoff,),
    )
    await conn.commit()


async def fanout_recent_exact_walmart_deals(
    bot: Any,
    *,
    event_limit: int = FANOUT_EVENT_LIMIT,
) -> GlobalDealFanoutResult:
    """Ingest and fan out exact-verified Walmart markdown events.

    The verification watermark prevents high-ranked rows from starving later
    rows. Every accepted event stores the exact candidate snapshot used to
    create it. Durable database leases prevent overlapping bot processes from
    sending the same event concurrently during deploys or failover.
    """

    db = getattr(bot, "db", None)
    if db is None or _FANOUT_LOCK.locked():
        return GlobalDealFanoutResult()

    async with _FANOUT_LOCK:
        await ensure_global_deal_event_tables(db)
        ingested = await _ingest_verified_queue_events(
            db,
            limit=FANOUT_INGEST_LIMIT,
        )
        claimed_events = await _claim_pending_events(
            db,
            limit=max(1, int(event_limit)),
        )
        if not claimed_events:
            return GlobalDealFanoutResult(
                candidates_loaded=ingested.loaded,
                exact_cards=ingested.exact_cards,
                new_events=ingested.new_events,
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

        for event in claimed_events:
            event_errors: list[str] = []

            for guild in guilds:
                guild_id = int(guild.guild_id)
                totals["guilds_checked"] += 1
                try:
                    threshold = await get_starting_deal_percent(db, guild_id)
                    public_result = await maybe_post_public_deal_cards(
                        bot=bot,
                        guild_id=guild_id,
                        cards=[event.card],
                        source_label="global_catalog_autoscan:exact_verified",
                        fallback_retailer="walmart",
                        min_public_discount=int(threshold),
                    )
                    totals["public_posts"] += int(public_result.posted)
                    decided = bool(
                        public_result.posted
                        or public_result.skipped_duplicate
                        or public_result.skipped_not_alertable
                        or public_result.skipped_disabled
                        or public_result.skipped_wrong_retailer
                    )
                    if public_result.errors and not decided:
                        totals["public_errors"] += len(public_result.errors)
                        event_errors.extend(public_result.errors)
                    elif public_result.errors:
                        log.info(
                            "Global Walmart fanout destination completed with note guild=%s deal=%s notes=%s",
                            guild_id,
                            event.deal_key,
                            list(public_result.errors),
                        )
                except Exception as error:  # noqa: BLE001 - one guild cannot block global fanout.
                    totals["public_errors"] += 1
                    event_errors.append(
                        f"guild {guild_id}: {type(error).__name__}: {error}"
                    )
                    log.exception(
                        "Global Walmart public fanout failed guild=%s deal=%s",
                        guild_id,
                        event.deal_key,
                    )

            for preference in preferences:
                totals["dm_preferences_checked"] += 1
                decision = match_dm_deal(preference, event.card)
                if not decision.matched:
                    continue
                totals["dm_matches"] += 1
                try:
                    if await dm_receipt_exists(
                        db,
                        user_id=preference.user_id,
                        deal_key=event.deal_key,
                    ):
                        continue
                    sent_today = await dm_alerts_sent_today(db, preference.user_id)
                    if sent_today >= preference.max_alerts_per_day:
                        continue
                    user = bot.get_user(preference.user_id)
                    if user is None:
                        user = await bot.fetch_user(preference.user_id)
                    await user.send(
                        embed=_personal_dm_embed(event.card, decision.reason)
                    )
                    await record_dm_receipt(
                        db,
                        user_id=preference.user_id,
                        deal_key=event.deal_key,
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
                        event.deal_key,
                    )

            if event_errors:
                await _release_event_with_error(
                    db,
                    deal_key=event.deal_key,
                    claim_token=event.claim_token,
                    errors=event_errors,
                )
            else:
                await _mark_event_processed(
                    db,
                    deal_key=event.deal_key,
                    claim_token=event.claim_token,
                )
                totals["events_processed"] += 1

        return GlobalDealFanoutResult(
            candidates_loaded=ingested.loaded,
            exact_cards=ingested.exact_cards,
            new_events=ingested.new_events,
            **totals,
        )


async def _ingest_verified_queue_events(db: Any, *, limit: int) -> _IngestResult:
    conn = db.require_conn()
    state_cursor = await conn.execute(
        f"""
        SELECT last_verified_at, last_item_id
        FROM {STATE_TABLE}
        WHERE state_key = ?
        """,
        (STATE_KEY,),
    )
    state = await state_cursor.fetchone()
    last_verified_at = str(_row_get(state, "last_verified_at", 0) or "")
    last_item_id = str(_row_get(state, "last_item_id", 1) or "")

    cursor = await conn.execute(
        f"""
        SELECT item_id, verified_at, snapshot_json
        FROM {QUEUE_TABLE}
        WHERE verified_at IS NOT NULL
          AND snapshot_json <> ''
          AND status = 'verified_markdown'
          AND (
              verified_at > ?
              OR (verified_at = ? AND item_id > ?)
          )
        ORDER BY verified_at ASC, item_id ASC
        LIMIT ?
        """,
        (
            last_verified_at,
            last_verified_at,
            last_item_id,
            max(1, int(limit)),
        ),
    )
    rows = await cursor.fetchall()
    if not rows:
        return _IngestResult()

    loaded = 0
    exact_cards = 0
    new_events = 0
    final_verified_at = last_verified_at
    final_item_id = last_item_id

    for row in rows:
        item_id = str(_row_get(row, "item_id", 0) or "")
        verified_at = str(_row_get(row, "verified_at", 1) or "")
        snapshot_json = str(_row_get(row, "snapshot_json", 2) or "")
        final_verified_at = verified_at
        final_item_id = item_id
        loaded += 1

        candidate = _candidate_from_snapshot(snapshot_json)
        card = _exact_card_for_candidate(candidate)
        if card is None:
            continue
        exact_cards += 1
        retailer = str(getattr(card, "retailer", None) or "walmart")
        deal_key = card_deal_key(card, retailer=retailer)
        if await _insert_event_if_new(
            db,
            deal_key=deal_key,
            snapshot_json=snapshot_json,
            verified_at=verified_at,
        ):
            new_events += 1

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        UPDATE {STATE_TABLE}
        SET last_verified_at = ?, last_item_id = ?, updated_at = ?
        WHERE state_key = ?
        """,
        (final_verified_at, final_item_id, now, STATE_KEY),
    )
    await conn.commit()
    return _IngestResult(
        loaded=loaded,
        exact_cards=exact_cards,
        new_events=new_events,
    )


async def _insert_event_if_new(
    db: Any,
    *,
    deal_key: str,
    snapshot_json: str,
    verified_at: str,
) -> bool:
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT 1 FROM {EVENT_TABLE} WHERE deal_key = ? LIMIT 1",
        (deal_key,),
    )
    if await cursor.fetchone() is not None:
        return False
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        INSERT INTO {EVENT_TABLE} (
            deal_key, snapshot_json, first_seen_at, source_verified_at,
            attempt_count, last_error, claim_token
        ) VALUES (?, ?, ?, ?, 0, '', '')
        ON CONFLICT(deal_key) DO NOTHING
        """,
        (deal_key, snapshot_json, now, verified_at),
    )
    await conn.commit()
    return True


async def _claim_pending_events(db: Any, *, limit: int) -> list[_ClaimedEvent]:
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT deal_key, snapshot_json
        FROM {EVENT_TABLE}
        WHERE processed_at IS NULL
          AND (lease_until IS NULL OR lease_until <= ?)
        ORDER BY first_seen_at ASC
        LIMIT ?
        """,
        (now_iso, max(1, int(limit))),
    )
    rows = await cursor.fetchall()
    claimed: list[_ClaimedEvent] = []
    lease_until = (now + timedelta(seconds=EVENT_LEASE_SECONDS)).isoformat()

    for row in rows:
        deal_key = str(_row_get(row, "deal_key", 0) or "")
        snapshot_json = str(_row_get(row, "snapshot_json", 1) or "")
        if not deal_key:
            continue
        token = uuid.uuid4().hex
        await conn.execute(
            f"""
            UPDATE {EVENT_TABLE}
            SET claim_token = ?, lease_until = ?, last_attempt_at = ?,
                attempt_count = attempt_count + 1
            WHERE deal_key = ?
              AND processed_at IS NULL
              AND (lease_until IS NULL OR lease_until <= ?)
            """,
            (token, lease_until, now_iso, deal_key, now_iso),
        )
        verify = await conn.execute(
            f"SELECT claim_token FROM {EVENT_TABLE} WHERE deal_key = ?",
            (deal_key,),
        )
        verify_row = await verify.fetchone()
        if str(_row_get(verify_row, "claim_token", 0) or "") != token:
            continue

        candidate = _candidate_from_snapshot(snapshot_json)
        card = _exact_card_for_candidate(candidate)
        if card is None:
            await _mark_event_processed(
                db,
                deal_key=deal_key,
                claim_token=token,
            )
            continue
        claimed.append(
            _ClaimedEvent(
                deal_key=deal_key,
                claim_token=token,
                card=card,
            )
        )

    await conn.commit()
    return claimed


def _exact_card_for_candidate(candidate: Any) -> Any | None:
    if candidate is None:
        return None
    aggregate = ProviderScanResult(
        provider_key="walmart",
        candidates=(candidate,),
        page=1,
        page_size=1,
        start_index=1,
        has_next_page=False,
    )
    cards = deal_scanner.build_walmart_cards(
        aggregate,
        min_discount=1,
        alerts_only=False,
    )
    normalize_exact_verified_walmart_cards(cards, min_discount=1)
    for card in cards:
        if is_public_deal_candidate(
            card,
            source_label="global_catalog_autoscan:exact_verified",
            min_discount=1,
        ):
            return card
    return None


async def _mark_event_processed(
    db: Any,
    *,
    deal_key: str,
    claim_token: str,
) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        UPDATE {EVENT_TABLE}
        SET processed_at = ?, last_error = '', claim_token = '', lease_until = NULL
        WHERE deal_key = ? AND claim_token = ?
        """,
        (now, deal_key, claim_token),
    )
    await conn.commit()


async def _release_event_with_error(
    db: Any,
    *,
    deal_key: str,
    claim_token: str,
    errors: list[str],
) -> None:
    conn = db.require_conn()
    text = " | ".join(" ".join(str(error).split()) for error in errors if error)
    if len(text) > 1000:
        text = text[:999] + "…"
    await conn.execute(
        f"""
        UPDATE {EVENT_TABLE}
        SET last_error = ?, claim_token = '', lease_until = NULL
        WHERE deal_key = ? AND claim_token = ?
        """,
        (text, deal_key, claim_token),
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
