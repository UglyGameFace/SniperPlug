from __future__ import annotations

import asyncio
import hashlib
import json
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
from sniperplug.services.ghost_guild_tombstones import load_ghost_tombstones
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_quality import walmart_item_id_from_url
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.dm_deal_alerts import (
    card_search_text,
    clear_dm_delivery_failures,
    dm_receipt_exists,
    list_enabled_dm_deal_alert_preferences,
    record_dm_delivery_failure,
    walmart_cash_cents,
)
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_personal_categories import category_key_for_card
from sniperplug.services.dm_delivery_policy import (
    dm_delivery_lock,
    evaluate_dm_delivery_gate,
    record_dm_delivery_receipt,
)
from sniperplug.services.dm_delivery_telemetry import (
    record_user_dm_decision,
    record_user_dm_outcome,
)
from sniperplug.services.public_deal_posts import (
    card_deal_key,
    maybe_post_public_deal_cards,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_from_snapshot,
)
from sniperplug.services.walmart_fanout_decisions import (
    WalmartGuildFanoutDecision,
    classify_public_fanout_result,
    decision_now,
    ensure_walmart_fanout_decision_table,
    prune_walmart_fanout_decisions,
    record_walmart_fanout_decisions,
)


FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
FANOUT_CLEANUP_FOLLOWUP_SECONDS = 5 * 60
FANOUT_CLEANUP_BATCH_SIZE = 100
FANOUT_EVENT_KEY_VERSION = "v2"
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
# An enrolled server can be missing from Discord's live guild cache during a
# gateway reconnect or outage. A deal is not acknowledged for such a server; the
# event is deferred (not blocking newer events) and retried, while servers that
# already received it are protected by durable post reservations. After this
# window the offer is too old to announce as current, so the event finalizes.
MISSING_GUILD_RETRY_SECONDS = 5 * 60
MISSING_GUILD_MAX_WAIT = timedelta(hours=6)
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
    dm_skipped_duplicate: int = 0
    dm_skipped_daily_cap: int = 0
    dm_skipped_diversity: int = 0
    dm_sent: int = 0
    dm_disabled: int = 0
    dm_errors: int = 0

    @property
    def public_skipped_duplicate(self) -> int:
        return (
            self.public_skipped_recent_duplicate
            + self.public_skipped_reserved_duplicate
        )

    @property
    def dm_filter_rejected(self) -> int:
        return max(0, int(self.dm_preferences_checked) - int(self.dm_matches))

    @property
    def deduped_event_versions(self) -> int:
        return max(0, int(self.exact_cards) - int(self.new_events))

    def summary_line(self) -> str:
        return (
            "global exact-deal fanout: "
            f"loaded **{self.candidates_loaded}** • exact cards **{self.exact_cards}** • "
            f"new event versions **{self.new_events}** • deduped versions **{self.deduped_event_versions}** • "
            f"processed **{self.events_processed}** • "
            f"guild checks/posts **{self.guilds_checked}/{self.public_posts}** • "
            f"public skips duplicate **{self.public_skipped_duplicate}** "
            f"(recent **{self.public_skipped_recent_duplicate}** • reserved **{self.public_skipped_reserved_duplicate}**) • "
            f"not alertable **{self.public_skipped_not_alertable}** • "
            f"disabled **{self.public_skipped_disabled}** • retailer **{self.public_skipped_wrong_retailer}** • "
            f"DM checked/matched/sent **{self.dm_preferences_checked}/{self.dm_matches}/{self.dm_sent}** • "
            f"DM skips filter/duplicate/cap/diversity **{self.dm_filter_rejected}/{self.dm_skipped_duplicate}/{self.dm_skipped_daily_cap}/{self.dm_skipped_diversity}** • "
            f"DM disabled **{self.dm_disabled}** • errors public/DM **{self.public_errors}/{self.dm_errors}**"
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
    source_verified_at: str = ""


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
    """Initialize fanout schema only; retention is post-delivery maintenance."""

    conn = db.require_conn()
    state = _state_for(conn)
    if state.schema_ready:
        return
    async with _lock(state, "schema_lock"):
        if state.schema_ready:
            return
        await legacy.ensure_global_deal_event_tables(db)
        await ensure_walmart_fanout_decision_table(db)
        await conn.execute(FANOUT_CURSOR_INDEX_SQL)
        await conn.commit()
        state.schema_ready = True
        state.next_cleanup_monotonic = (
            time.monotonic() + FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS
        )


async def maybe_prune_global_deal_event_storage_bounded(db: Any) -> tuple[int, int]:
    """Prune fanout events/provenance only after current deal delivery is done."""

    conn = db.require_conn()
    state = _state_for(conn)
    if not state.schema_ready:
        await ensure_global_deal_event_tables_once(db)
    if time.monotonic() < state.next_cleanup_monotonic:
        return 0, 0

    async with _lock(state, "cleanup_lock"):
        if time.monotonic() < state.next_cleanup_monotonic:
            return 0, 0
        event_rows = await legacy.prune_global_deal_events_bounded(
            db,
            limit=FANOUT_CLEANUP_BATCH_SIZE,
        )
        decision_rows = await prune_walmart_fanout_decisions(
            db,
            limit=FANOUT_CLEANUP_BATCH_SIZE,
        )
        state.next_cleanup_monotonic = time.monotonic() + (
            FANOUT_CLEANUP_FOLLOWUP_SECONDS
            if (
                event_rows >= FANOUT_CLEANUP_BATCH_SIZE
                or decision_rows >= FANOUT_CLEANUP_BATCH_SIZE
            )
            else FANOUT_SCHEMA_CLEANUP_INTERVAL_SECONDS
        )
        return event_rows, decision_rows


async def fanout_recent_exact_walmart_deals(
    bot: Any,
    *,
    event_limit: int = legacy.FANOUT_EVENT_LIMIT,
) -> GlobalDealFanoutResult:
    """Bulk-ingest and transparently fan out exact Walmart deal events.

    Every public destination decision is persisted before the global event is
    finalized. That historical provenance lets owner health distinguish a true
    missing-post gap from an event that was legitimately below threshold, muted,
    quality-blocked, or deduped when fanout actually ran. Retention is owned by
    the runner after fanout so maintenance cannot sit in front of a ready alert.
    """

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

        load_result = await list_live_public_alert_guilds(
            db,
            bot,
            required_retailer="walmart",
        )
        guilds = list(load_result.guilds)
        awaited_guild_ids = await _enrolled_walmart_guilds_not_live(
            db,
            bot,
            load_result,
        )
        superseded = await _superseded_event_keys(db, claimed_events)
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
            "dm_skipped_duplicate": 0,
            "dm_skipped_daily_cap": 0,
            "dm_skipped_diversity": 0,
            "dm_sent": 0,
            "dm_disabled": 0,
            "dm_errors": 0,
        }
        processed: list[tuple[str, str]] = []
        failed: list[tuple[str, str, str]] = []
        deferred: list[tuple[str, str, str]] = []
        fanout_decisions: list[WalmartGuildFanoutDecision] = []
        now_utc = datetime.now(timezone.utc)

        for event in claimed_events:
            if event.deal_key in superseded:
                # The exact offer was re-verified at a different price or lost
                # its markdown status since this event was created. Never
                # announce the old price as current.
                log.info(
                    "Global Walmart event superseded before delivery deal=%s reason=%s",
                    event.deal_key,
                    superseded[event.deal_key],
                )
                processed.append((event.deal_key, event.claim_token))
                continue
            event_errors: list[str] = []
            event_public_posts = 0
            for guild in guilds:
                guild_id = int(guild.guild_id)
                totals["guilds_checked"] += 1
                threshold = 0
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
                    outcome, detail = classify_public_fanout_result(
                        public_result,
                        discount=_card_discount(event.card),
                        threshold=int(threshold),
                    )
                    fanout_decisions.append(
                        decision_now(
                            event_key=event.deal_key,
                            guild_id=guild_id,
                            outcome=outcome,
                            threshold=int(threshold),
                            detail=detail,
                        )
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
                    error_text = f"{type(error).__name__}: {error}"
                    event_errors.append(f"guild {guild_id}: {error_text}")
                    fanout_decisions.append(
                        decision_now(
                            event_key=event.deal_key,
                            guild_id=guild_id,
                            outcome="error",
                            threshold=int(threshold),
                            detail=error_text,
                        )
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

            delivery_deal_key = _delivery_deal_key(event.card)
            for preference in preferences:
                totals["dm_preferences_checked"] += 1
                decision = match_dm_deal(preference, event.card)
                record_user_dm_decision(
                    preference.user_id,
                    event_key=event.deal_key,
                    decision=decision,
                )
                if not decision.matched:
                    continue
                totals["dm_matches"] += 1
                try:
                    async with dm_delivery_lock(preference.user_id):
                        if await dm_receipt_exists(
                            db,
                            user_id=preference.user_id,
                            deal_key=delivery_deal_key,
                        ):
                            totals["dm_skipped_duplicate"] += 1
                            record_user_dm_outcome(preference.user_id, "duplicate")
                            continue
                        gate = await evaluate_dm_delivery_gate(
                            db,
                            preference=preference,
                            decision=decision,
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
                            embed=legacy._personal_dm_embed(event.card, decision.reason)
                        )
                        await record_dm_delivery_receipt(
                            db,
                            user_id=preference.user_id,
                            deal_key=delivery_deal_key,
                            category_family=gate.category_family,
                        )
                        await clear_dm_delivery_failures(
                            db,
                            user_id=preference.user_id,
                        )
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
                except Exception as error:  # noqa: BLE001
                    totals["dm_errors"] += 1
                    record_user_dm_outcome(preference.user_id, "error")
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
            elif awaited_guild_ids and not _event_too_old(event, now_utc):
                deferred.append(
                    (
                        event.deal_key,
                        event.claim_token,
                        "awaiting enrolled guilds not in live Discord cache: "
                        + ",".join(str(gid) for gid in sorted(awaited_guild_ids))[:400],
                    )
                )
            else:
                if awaited_guild_ids:
                    log.warning(
                        "Global Walmart event finalized without delivery to absent guilds "
                        "deal=%s guilds=%s max_wait_h=%s",
                        event.deal_key,
                        sorted(awaited_guild_ids),
                        MISSING_GUILD_MAX_WAIT.total_seconds() / 3600,
                    )
                processed.append((event.deal_key, event.claim_token))
                totals["events_processed"] += 1

        # Decision provenance is written before events are marked processed. If
        # Turso cannot persist this small bounded batch, leave the events leased
        # and retry later; public post reservations and DM receipts prevent sends
        # from duplicating on that retry.
        await record_walmart_fanout_decisions(db, fanout_decisions)
        await _finalize_claimed_events_bulk(
            db,
            processed=processed,
            failed=failed,
        )
        await _defer_claimed_events_bulk(db, deferred)
        if deferred:
            log.warning(
                "Global Walmart fanout deferred %s event(s) for enrolled guilds "
                "missing from the live Discord cache guilds=%s",
                len(deferred),
                sorted(awaited_guild_ids),
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
        deal_key = _fanout_event_key(card, retailer=retailer)
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
        RETURNING deal_key, snapshot_json, source_verified_at
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
                source_verified_at=str(
                    legacy._row_get(row, "source_verified_at", 2) or ""
                ),
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


async def _enrolled_walmart_guilds_not_live(
    db: Any,
    bot: Any,
    load_result: Any,
) -> set[int]:
    """Enrolled Walmart destinations that Discord's live cache does not show.

    ``list_live_public_alert_guilds`` deliberately returns nothing when the bot
    has no live guilds at all, so that case re-reads enrollment directly.
    Tombstoned ghost rows (servers the bot genuinely left) are never awaited.
    """

    if list(getattr(bot, "guilds", []) or []):
        candidates = set(int(gid) for gid in load_result.stale_visible_ids)
        candidates -= set(int(gid) for gid in load_result.tombstoned_visible_ids)
    else:
        conn = db.require_conn()
        cursor = await conn.execute(
            "SELECT CAST(guild_id AS TEXT) AS guild_id FROM guild_public_alert_settings "
            "WHERE enabled = 1 AND channel_id IS NOT NULL"
        )
        candidates = set()
        for row in await cursor.fetchall():
            try:
                candidates.add(int(str(legacy._row_get(row, "guild_id", 0))))
            except (TypeError, ValueError):
                continue
        if candidates:
            tombstoned = await load_ghost_tombstones(
                db.require_conn(),
                guild_ids=None,
            )
            candidates -= set(int(gid) for gid in tombstoned)
    awaited: set[int] = set()
    for guild_id in candidates:
        try:
            config = await get_public_alert_config(db, guild_id)
        except Exception:  # noqa: BLE001 - unreadable config: do not drop it.
            awaited.add(guild_id)
            continue
        retailers = {
            str(value or "").strip().lower()
            for value in (config.get("retailers") or ())
        }
        if config.get("enabled") and config.get("channel_id") and "walmart" in retailers:
            awaited.add(guild_id)
    return awaited


def _event_too_old(event: "_ClaimedEvent", now: datetime) -> bool:
    try:
        verified = datetime.fromisoformat(str(event.source_verified_at))
    except (TypeError, ValueError):
        return True
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    return now - verified > MISSING_GUILD_MAX_WAIT


async def _superseded_event_keys(
    db: Any,
    events: list["_ClaimedEvent"],
) -> dict[str, str]:
    """Events whose exact queue row no longer proves the same current offer."""

    wanted: dict[str, list["_ClaimedEvent"]] = {}
    for event in events:
        item_id = walmart_item_id_from_url(
            getattr(event.card, "direct_product_url", None)
            or getattr(event.card, "url", None)
        )
        if item_id:
            wanted.setdefault(item_id, []).append(event)
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    cursor = await db.require_conn().execute(
        f"SELECT item_id, status, exact_current_cents FROM {QUEUE_TABLE} "
        f"WHERE item_id IN ({placeholders})",
        tuple(wanted),
    )
    rows = {
        str(legacy._row_get(row, "item_id", 0) or ""): row
        for row in await cursor.fetchall()
    }
    superseded: dict[str, str] = {}
    for item_id, item_events in wanted.items():
        row = rows.get(item_id)
        for event in item_events:
            if row is None:
                superseded[event.deal_key] = "queue_row_missing"
                continue
            status = str(legacy._row_get(row, "status", 1) or "")
            if status != "verified_markdown":
                # Transient states (pending/verifying/retry) are re-checks in
                # flight, not evidence the offer changed; keep the event.
                if status.startswith("verified_") or status == "not_buyable":
                    superseded[event.deal_key] = f"status:{status}"
                continue
            card_cents = _money_cents(getattr(event.card, "current_price", None))
            row_cents = _int_value(legacy._row_get(row, "exact_current_cents", 2))
            if card_cents is not None and row_cents and card_cents != row_cents:
                superseded[event.deal_key] = f"price:{card_cents}->{row_cents}"
    return superseded


async def _defer_claimed_events_bulk(
    db: Any,
    deferred: list[tuple[str, str, str]],
) -> None:
    if not deferred:
        return
    conn = db.require_conn()
    retry_at = (
        datetime.now(timezone.utc) + timedelta(seconds=MISSING_GUILD_RETRY_SECONDS)
    ).isoformat()
    placeholders = ",".join("(?, ?, ?)" for _ in deferred)
    params = tuple(value for row in deferred for value in row)
    await conn.execute(
        f"""
        WITH deferred(deal_key, claim_token, last_error) AS (
            VALUES {placeholders}
        )
        UPDATE {legacy.EVENT_TABLE} AS target
        SET last_error = deferred.last_error, claim_token = '',
            lease_until = ?
        FROM deferred
        WHERE target.deal_key = deferred.deal_key
          AND target.claim_token = deferred.claim_token
        """,
        (*params, retry_at),
    )
    await conn.commit()


def _delivery_deal_key(card: Any) -> str:
    retailer = str(getattr(card, "retailer", None) or "walmart")
    return card_deal_key(card, retailer=retailer)


def _fanout_event_key(card: Any, *, retailer: str) -> str:
    """Version a fanout event by fields that can change alert eligibility.

    Delivery dedupe deliberately remains the legacy current-price key. This
    versioned event key only controls whether a newly verified proof state is
    re-evaluated by public and personal filters. A subscriber who already
    received the same offer at the same current price therefore remains
    deduped, while a previously rejected same-price offer can be reconsidered
    when its trusted reference, score, category/search text, Walmart Cash, or
    exact selected-offer presentation materially changes.
    """

    delivery_key = card_deal_key(card, retailer=retailer)
    payload = {
        "current_cents": _money_cents(
            getattr(card, "api_current_price", None)
            or getattr(card, "current_price", None)
        ),
        "reference_cents": _money_cents(
            getattr(card, "api_reference_price", None)
            or getattr(card, "typical_price", None)
        ),
        "discount_bps": _percent_bps(
            getattr(card, "api_discount_percent", None)
            or getattr(card, "discount", None)
        ),
        "score": _int_value(getattr(card, "score", None)),
        "category": str(category_key_for_card(card) or "uncategorized"),
        "walmart_cash_cents": int(walmart_cash_cents(card)),
        "search_text": " ".join(card_search_text(card).split())[:8000],
        "should_alert": _optional_bool(getattr(card, "should_alert", None)),
        "seller": _compact(getattr(card, "seller_name", None), 240),
        "variant": _compact(getattr(card, "variant_label", None), 240),
        "condition": _compact(getattr(card, "condition", None), 120),
        "fulfillment": _compact(getattr(card, "fulfillment_type", None), 120),
        "stock": _compact(getattr(card, "stock_status", None), 120),
        "can_add_to_cart": _optional_bool(getattr(card, "can_add_to_cart", None)),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:20]
    return f"{delivery_key}:proof:{FANOUT_EVENT_KEY_VERSION}:{digest}"


def _money_cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return int(round(amount * 100))


def _percent_bps(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


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
        "not_alertable=%s not_alertable_reasons=%s disabled=%s wrong_retailer=%s label=%s",
        guild_id,
        event.deal_key,
        threshold,
        _card_discount(event.card),
        getattr(event.card, "current_price", None),
        int(getattr(result, "posted", 0) or 0),
        int(getattr(result, "skipped_recent_alert_duplicate", 0) or 0),
        int(getattr(result, "skipped_reserved_duplicate", 0) or 0),
        int(getattr(result, "skipped_not_alertable", 0) or 0),
        list(getattr(result, "not_alertable_reasons", ()) or ()),
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
