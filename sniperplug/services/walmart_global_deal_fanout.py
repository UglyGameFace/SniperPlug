from __future__ import annotations

import asyncio
import logging
import time
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
EVENT_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
EVENT_CLEANUP_BATCH_SIZE = 100
_FANOUT_LOCK = asyncio.Lock()
_LAST_EVENT_CLEANUP_MONOTONIC = 0.0
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
    """Initialize fanout schema only; retention must never precede delivery."""

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
    await conn.commit()


async def prune_global_deal_events_bounded(
    db: Any,
    *,
    limit: int = EVENT_CLEANUP_BATCH_SIZE,
) -> int:
    """Delete one bounded batch of already-processed, expired fanout events."""

    conn = db.require_conn()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS)
    ).isoformat()
    bounded_limit = max(1, min(int(limit), 500))
    cursor = await conn.execute(
        f"""
        SELECT deal_key
        FROM {EVENT_TABLE}
        WHERE processed_at IS NOT NULL AND first_seen_at < ?
        ORDER BY first_seen_at ASC
        LIMIT ?
        """,
        (cutoff, bounded_limit),
    )
    keys = [
        str(_row_get(row, "deal_key", 0) or "").strip()
        for row in await cursor.fetchall()
    ]
    keys = [key for key in keys if key]
    if not keys:
        return 0
    placeholders = ",".join("?" for _ in keys)
    await conn.execute(
        f"DELETE FROM {EVENT_TABLE} WHERE deal_key IN ({placeholders})",
        tuple(keys),
    )
    await conn.commit()
    return len(keys)


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
