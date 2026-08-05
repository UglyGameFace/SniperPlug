from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from sniperplug.services import walmart_global_deal_fanout as legacy
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
from sniperplug.services.public_deal_posts import (
    card_deal_key,
    maybe_post_public_deal_cards,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_from_snapshot,
)


FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
FANOUT_CURSOR_INDEX = f"idx_{QUEUE_TABLE}_fanout_cursor"
FANOUT_CURSOR_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS {FANOUT_CURSOR_INDEX}
ON {QUEUE_TABLE} (verified_at, item_id)
WHERE status = 'verified_markdown'
  AND verified_at IS NOT NULL
  AND snapshot_json <> ''
"""
FANOUT_CURSOR_KEYS_SQL = f"""
SELECT item_id, verified_at
FROM {QUEUE_TABLE} INDEXED BY {FANOUT_CURSOR_INDEX}
WHERE status = 'verified_markdown'
  AND verified_at IS NOT NULL
  AND snapshot_json <> ''
  AND (verified_at, item_id) > (?, ?)
ORDER BY verified_at ASC, item_id ASC
LIMIT ?
"""
_STATE_ATTR = "_sniperplug_walmart_fanout_bulk_state"
_FALLBACK_STATES: dict[int, "_FanoutRuntimeState"] = {}
log = logging.getLogger("sniperplug.autoscan.fanout")


@dataclass
class _FanoutRuntimeState:
    connection: Any
    schema_ready: bool = False
    schema_lock: asyncio.Lock | None = None
    cleanup_lock: asyncio.Lock | None = None
    next_cleanup_monotonic: float = 0.0


@dataclass(frozen=True)
class GlobalDealFanoutResult:
    candidates_loaded: int = 0
    exact_cards: int = 0
    new_events: int = 0
    events_processed: int = 0
    guilds_checked: int = 0
    public_posts: int = 0
    public_skipped_recent_duplicate: int = 0
    public_skipped_reserved_duplicate: int = 0
    public_skipped_not_alertable: int = 0
    public_skipped_disabled: int = 0
    public_skipped_wrong_retailer: int = 0
    public_errors: int = 0
    dm_preferences_checked: int = 0
    dm_matches: int = 0
    dm_sent: int = 0
    dm_disabled: int = 0
    dm_errors: int = 0

    @property
    def public_skipped_duplicate(self) -> int:
        return (
            self.public_skipped_recent_duplicate
            + self.public_skipped_reserved_duplicate
        )

    def summary_line(self) -> str:
        return (
            "global exact-deal fanout: "
            f"loaded **{self.candidates_loaded}** • exact cards **{self.exact_cards}** • "
            f"new events **{self.new_events}** • processed **{self.events_processed}** • "
            f"guild checks/posts **{self.guilds_checked}/{self.public_posts}** • "
            f"public skips duplicate **{self.public_skipped_duplicate}** "
            f"(recent **{self.public_skipped_recent_duplicate}** • reserved **{self.public_skipped_reserved_duplicate}**) • "
            f"not alertable **{self.public_skipped_not_alertable}** • "
            f"disabled **{self.public_skipped_disabled}** • retailer **{self.public_skipped_wrong_retailer}** • "
            f"DM sent **{self.dm_sent}** • errors public/DM **{self.public_errors}/{self.dm_errors}**"
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


@dataclass(frozen=True)
class _CursorKey:
    item_id: str
    verified_at: str


def _state_for(conn: Any) -> _FanoutRuntimeState:
    state = getattr(conn, _STATE_ATTR, None)
    if isinstance(state, _FanoutRuntimeState):
        return state
    state = _FALLBACK_STATES.get(id(conn))
    if state is None or state.connection is not conn:
        state = _FanoutRuntimeState(connection=conn)
        try:
            setattr(conn, _STATE_ATTR, state)
        except Exception:
            _FALLBACK_STATES[id(conn)] = state
    return state


def _lock(state: _FanoutRuntimeState, name: str) -> asyncio.Lock:
    value = getattr(state, name)
    if value is None:
        value = asyncio.Lock()
        setattr(state, name, value)
    return value


async def ensure_global_deal_event_tables_once(db: Any) -> None:
    """Initialize durable fanout schema and its queue cursor index once."""

    conn = db.require_conn()
    state = _state_for(conn)
    if not state.schema_ready:
        async with _lock(state, "schema_lock"):
            if not state.schema_ready:
                await legacy.ensure_global_deal_event_tables(db)
                await conn.execute(FANOUT_CURSOR_INDEX_SQL)
                await conn.commit()
                state.schema_ready = True
                state.next_cleanup_monotonic = (
                    time.monotonic() + FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS
                )
                return

    if time.monotonic() < state.next_cleanup_monotonic:
        return
    async with _lock(state, "cleanup_lock"):
        if time.monotonic() < state.next_cleanup_monotonic:
            return
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=legacy.EVENT_RETENTION_DAYS)
        ).isoformat()
        await conn.execute(
            f"DELETE FROM {legacy.EVENT_TABLE} "
            "WHERE processed_at IS NOT NULL AND first_seen_at < ?",
            (cutoff,),
        )
        await conn.commit()
        state.next_cleanup_monotonic = (
            time.monotonic() + FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS
        )


async def fanout_recent_exact_walmart_deals(
    bot: Any,
    *,
    event_limit: int = legacy.FANOUT_EVENT_LIMIT,
) -> GlobalDealFanoutResult:
    """Bulk-ingest and transparently fan out exact Walmart deal events."""

    db = getattr(bot, "db", None)
    if db is None or legacy._FANOUT_LOCK.locked():
        return GlobalDealFanoutResult()

    async with legacy._FANOUT_LOCK:
        await ensure_global_deal_event_tables_once(db)
        ingested = await _ingest_verified_queue_events_bulk(
            db,
            limit=legacy.FANOUT_INGEST_LIMIT,
        )
        claimed_events = await _claim_pending_events_bulk(
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
            "public_skipped_recent_duplicate": 0,
            "public_skipped_reserved_duplicate": 0,
            "public_skipped_not_alertable": 0,
            "public_skipped_disabled": 0,
            "public_skipped_wrong_retailer": 0,
            "public_errors": 0,
            "dm_preferences_checked": 0,
            "dm_matches": 0,
            "dm_sent": 0,
            "dm_disabled": 0,
            "dm_errors": 0,
        }
        processed: list[tuple[str, str]] = []
        failed: list[tuple[str, str, str]] = []

        for event in claimed_events:
            event_errors: list[str] = []
            event_public_posts = 0
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
                    posted = int(public_result.posted)
                    event_public_posts += posted
                    totals["public_posts"] += posted
                    totals["public_skipped_recent_duplicate"] += int(
                        public_result.skipped_recent_alert_duplicate
                    )
                    totals["public_skipped_reserved_duplicate"] += int(
                        public_result.skipped_reserved_duplicate
                    )
                    totals["public_skipped_not_alertable"] += int(
                        public_result.skipped_not_alertable
                    )
                    totals["public_skipped_disabled"] += int(
                        public_result.skipped_disabled
                    )
                    totals["public_skipped_wrong_retailer"] += int(
                        public_result.skipped_wrong_retailer
                    )
                    decided = bool(
                        posted
                        or public_result.skipped_duplicate
                        or public_result.skipped_not_alertable
                        or public_result.skipped_disabled
                        or public_result.skipped_wrong_retailer
                    )
                    if not posted:
                        _log_public_skip(
                            guild_id=guild_id,
                            event=event,
                            threshold=int(threshold),
                            result=public_result,
                        )
                    if public_result.errors and not decided:
                        totals["public_errors"] += len(public_result.errors)
                        event_errors.extend(
                            f"guild {guild_id}: {note}"
                            for note in public_result.errors
                        )
                    elif public_result.errors:
                        log.info(
                            "Global Walmart fanout destination completed with note guild=%s deal=%s notes=%s",
                            guild_id,
                            event.deal_key,
                            list(public_result.errors),
                        )
                except Exception as error:  # noqa: BLE001
                    totals["public_errors"] += 1
                    event_errors.append(
                        f"guild {guild_id}: {type(error).__name__}: {error}"
                    )
                    log.exception(
                        "Global Walmart public fanout failed guild=%s deal=%s",
                        guild_id,
                        event.deal_key,
                    )

            if guilds and event_public_posts == 0:
                log.info(
                    "Global Walmart event produced no public post deal=%s guilds_checked=%s label=%s discount=%s price=%s",
                    event.deal_key,
                    len(guilds),
                    _compact(getattr(event.card, "label", None), 100),
                    _card_discount(event.card),
                    getattr(event.card, "current_price", None),
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
                        embed=legacy._personal_dm_embed(event.card, decision.reason)
                    )
                    await record_dm_receipt(
                        db,
                        user_id=preference.user_id,
                        deal_key=event.deal_key,
                    )
                    await clear_dm_delivery_failures(
                        db,
                        user_id=preference.user_id,
                    )
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
                except Exception as error:  # noqa: BLE001
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
                failed.append(
                    (
                        event.deal_key,
                        event.claim_token,
                        _error_text(event_errors),
                    )
                )
            else:
                processed.append((event.deal_key, event.claim_token))
                totals["events_processed"] += 1

        await _finalize_claimed_events_bulk(
            db,
            processed=processed,
            failed=failed,
        )
        return GlobalDealFanoutResult(
            candidates_loaded=ingested.loaded,
            exact_cards=ingested.exact_cards,
            new_events=ingested.new_events,
            **totals,
        )


async def _ingest_verified_queue_events_bulk(
    db: Any,
    *,
    limit: int,
) -> _IngestResult:
    conn = db.require_conn()
    last_verified_at, last_item_id = await _load_fanout_watermark(conn)
    keys = await _select_verified_queue_cursor_keys(
        conn,
        last_verified_at=last_verified_at,
        last_item_id=last_item_id,
        limit=limit,
    )
    if not keys:
        return _IngestResult()

    rows = await _load_cursor_snapshots(conn, keys)
    loaded = 0
    exact_cards = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    event_rows: dict[str, tuple[str, str, str, str]] = {}

    for key, snapshot_json in rows:
        loaded += 1
        candidate = _candidate_from_snapshot(snapshot_json)
        card = legacy._exact_card_for_candidate(candidate)
        if card is None:
            continue
        exact_cards += 1
        retailer = str(getattr(card, "retailer", None) or "walmart")
        deal_key = card_deal_key(card, retailer=retailer)
        event_rows[deal_key] = (
            deal_key,
            snapshot_json,
            now_iso,
            key.verified_at,
        )

    new_events = 0
    if event_rows:
        values = list(event_rows.values())
        placeholders = ",".join("(?, ?, ?, ?, 0, '', '')" for _ in values)
        params = tuple(value for row in values for value in row)
        inserted = await conn.execute(
            f"""
            INSERT INTO {legacy.EVENT_TABLE} (
                deal_key, snapshot_json, first_seen_at, source_verified_at,
                attempt_count, last_error, claim_token
            ) VALUES {placeholders}
            ON CONFLICT(deal_key) DO NOTHING
            RETURNING deal_key
            """,
            params,
        )
        new_events = len(await inserted.fetchall())

    final_key = keys[-1]
    await conn.execute(
        f"""
        UPDATE {legacy.STATE_TABLE}
        SET last_verified_at = ?, last_item_id = ?, updated_at = ?
        WHERE state_key = ?
        """,
        (
            final_key.verified_at,
            final_key.item_id,
            datetime.now(timezone.utc).isoformat(),
            legacy.STATE_KEY,
        ),
    )
    await conn.commit()
    return _IngestResult(
        loaded=loaded,
        exact_cards=exact_cards,
        new_events=new_events,
    )


async def _load_fanout_watermark(conn: Any) -> tuple[str, str]:
    cursor = await conn.execute(
        f"""
        SELECT last_verified_at, last_item_id
        FROM {legacy.STATE_TABLE}
        WHERE state_key = ?
        """,
        (legacy.STATE_KEY,),
    )
    row = await cursor.fetchone()
    if row is None:
        return "", ""
    return (
        str(legacy._row_get(row, "last_verified_at", 0) or ""),
        str(legacy._row_get(row, "last_item_id", 1) or ""),
    )


async def _select_verified_queue_cursor_keys(
    conn: Any,
    *,
    last_verified_at: str,
    last_item_id: str,
    limit: int,
) -> list[_CursorKey]:
    cursor = await conn.execute(
        FANOUT_CURSOR_KEYS_SQL,
        (
            str(last_verified_at or ""),
            str(last_item_id or ""),
            max(1, int(limit)),
        ),
    )
    rows = await cursor.fetchall()
    keys: list[_CursorKey] = []
    for row in rows:
        item_id = str(legacy._row_get(row, "item_id", 0) or "")
        verified_at = str(legacy._row_get(row, "verified_at", 1) or "")
        if item_id and verified_at:
            keys.append(_CursorKey(item_id=item_id, verified_at=verified_at))
    return keys


async def _load_cursor_snapshots(
    conn: Any,
    keys: list[_CursorKey],
) -> list[tuple[_CursorKey, str]]:
    if not keys:
        return []

    placeholders = ",".join("(?, ?, ?)" for _ in keys)
    params: tuple[Any, ...] = tuple(
        value
        for ordinal, key in enumerate(keys)
        for value in (key.item_id, key.verified_at, ordinal)
    )
    cursor = await conn.execute(
        f"""
        WITH picked(item_id, verified_at, ordinal) AS (
            VALUES {placeholders}
        )
        SELECT picked.item_id, picked.verified_at, queue.snapshot_json
        FROM picked
        JOIN {QUEUE_TABLE} AS queue
          ON queue.item_id = picked.item_id
         AND queue.verified_at = picked.verified_at
        WHERE queue.status = 'verified_markdown'
          AND queue.snapshot_json <> ''
        ORDER BY picked.ordinal ASC
        """,
        params,
    )
    rows = await cursor.fetchall()
    loaded: list[tuple[_CursorKey, str]] = []
    for row in rows:
        item_id = str(legacy._row_get(row, "item_id", 0) or "")
        verified_at = str(legacy._row_get(row, "verified_at", 1) or "")
        snapshot_json = str(legacy._row_get(row, "snapshot_json", 2) or "")
        if not item_id or not verified_at or not snapshot_json:
            continue
        loaded.append(
            (
                _CursorKey(item_id=item_id, verified_at=verified_at),
                snapshot_json,
            )
        )
    return loaded


async def _claim_pending_events_bulk(
    db: Any,
    *,
    limit: int,
) -> list[_ClaimedEvent]:
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lease_until = (
        now + timedelta(seconds=legacy.EVENT_LEASE_SECONDS)
    ).isoformat()
    token = uuid.uuid4().hex
    cursor = await conn.execute(
        f"""
        WITH picked AS (
            SELECT deal_key
            FROM {legacy.EVENT_TABLE}
            WHERE processed_at IS NULL
              AND (lease_until IS NULL OR lease_until <= ?)
            ORDER BY first_seen_at ASC
            LIMIT ?
        )
        UPDATE {legacy.EVENT_TABLE}
        SET claim_token = ?, lease_until = ?, last_attempt_at = ?,
            attempt_count = attempt_count + 1
        WHERE deal_key IN (SELECT deal_key FROM picked)
          AND processed_at IS NULL
          AND (lease_until IS NULL OR lease_until <= ?)
        RETURNING deal_key, snapshot_json
        """,
        (
            now_iso,
            max(1, int(limit)),
            token,
            lease_until,
            now_iso,
            now_iso,
        ),
    )
    rows = await cursor.fetchall()
    await conn.commit()

    claimed: list[_ClaimedEvent] = []
    invalid: list[tuple[str, str]] = []
    for row in rows:
        deal_key = str(legacy._row_get(row, "deal_key", 0) or "")
        snapshot_json = str(legacy._row_get(row, "snapshot_json", 1) or "")
        if not deal_key:
            continue
        candidate = _candidate_from_snapshot(snapshot_json)
        card = legacy._exact_card_for_candidate(candidate)
        if card is None:
            invalid.append((deal_key, token))
            continue
        claimed.append(
            _ClaimedEvent(
                deal_key=deal_key,
                claim_token=token,
                card=card,
            )
        )

    if invalid:
        await _finalize_claimed_events_bulk(
            db,
            processed=invalid,
            failed=[],
        )
    return claimed


async def _finalize_claimed_events_bulk(
    db: Any,
    *,
    processed: list[tuple[str, str]],
    failed: list[tuple[str, str, str]],
) -> None:
    if not processed and not failed:
        return
    conn = db.require_conn()
    if processed:
        placeholders = ",".join("(?, ?)" for _ in processed)
        params = tuple(value for row in processed for value in row)
        await conn.execute(
            f"""
            WITH finalized(deal_key, claim_token) AS (
                VALUES {placeholders}
            )
            UPDATE {legacy.EVENT_TABLE} AS target
            SET processed_at = ?, last_error = '', claim_token = '',
                lease_until = NULL
            FROM finalized
            WHERE target.deal_key = finalized.deal_key
              AND target.claim_token = finalized.claim_token
            """,
            (
                *params,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    if failed:
        placeholders = ",".join("(?, ?, ?)" for _ in failed)
        params = tuple(value for row in failed for value in row)
        await conn.execute(
            f"""
            WITH released(deal_key, claim_token, last_error) AS (
                VALUES {placeholders}
            )
            UPDATE {legacy.EVENT_TABLE} AS target
            SET last_error = released.last_error, claim_token = '',
                lease_until = NULL
            FROM released
            WHERE target.deal_key = released.deal_key
              AND target.claim_token = released.claim_token
            """,
            params,
        )
    await conn.commit()


def _log_public_skip(
    *,
    guild_id: int,
    event: _ClaimedEvent,
    threshold: int,
    result: Any,
) -> None:
    log.info(
        "Global Walmart public destination decision guild=%s deal=%s threshold=%s "
        "discount=%s price=%s posted=%s recent_duplicate=%s reserved_duplicate=%s "
        "not_alertable=%s disabled=%s wrong_retailer=%s label=%s",
        guild_id,
        event.deal_key,
        threshold,
        _card_discount(event.card),
        getattr(event.card, "current_price", None),
        int(getattr(result, "posted", 0) or 0),
        int(getattr(result, "skipped_recent_alert_duplicate", 0) or 0),
        int(getattr(result, "skipped_reserved_duplicate", 0) or 0),
        int(getattr(result, "skipped_not_alertable", 0) or 0),
        int(getattr(result, "skipped_disabled", 0) or 0),
        int(getattr(result, "skipped_wrong_retailer", 0) or 0),
        _compact(getattr(event.card, "label", None), 100),
    )


def _card_discount(card: Any) -> float | None:
    for value in (
        getattr(card, "api_discount_percent", None),
        getattr(card, "discount", None),
    ):
        try:
            if value is not None:
                return round(float(value), 2)
        except (TypeError, ValueError):
            continue
    return None


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _error_text(errors: list[str]) -> str:
    text = " | ".join(_compact(error, 500) for error in errors if error)
    if len(text) > 1000:
        return text[:999] + "…"
    return text
