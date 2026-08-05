from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_global_deal_fanout as legacy
from sniperplug.services.deal_category_preferences import decide_category
from sniperplug.services.deal_feedback import (
    build_deal_feedback_view,
    build_feedback_target,
)
from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.manual_review_share import share_review_card
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import (
    PUBLIC_ALERT_KEY,
    RESERVATION_STALE_MINUTES,
    card_deal_key,
    card_product_key,
    ensure_public_post_tables,
    finalize_successful_public_post,
    mark_public_deal_sending,
    maybe_post_public_deal_cards,
    release_public_deal_reservation,
    reserve_public_deal_post,
    resolve_public_alert_channel,
)
from sniperplug.services.public_deal_quality import (
    is_public_deal_candidate,
    structured_discount,
)
from sniperplug.services.walmart_delivery_health import SOURCE_LABEL
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_from_snapshot,
    ensure_walmart_exact_verification_queue,
)


RECOVERY_ACTION_TABLE = "walmart_delivery_recovery_actions"
RECOVERY_WINDOW_HOURS = 72
RECOVERY_EVENT_LIMIT = 25
OWNER_OVERRIDE_SOURCE_LABEL = "owner_override:exact_walmart"
OWNER_OVERRIDE_POST_PREFIX = "owner_override:v1"
OWNER_RECHECK_SOURCE_LABEL = "owner_recovery:exact_recheck"
TERMINAL_QUEUE_STATUSES = ("incomplete_identity", "identity_mismatch")

SOFT_OVERRIDE_OUTCOMES = frozenset(
    {
        "below_threshold",
        "category_muted",
        "stale_reservation",
        "eligible_without_post",
        "fanout_error",
    }
)
SAFE_RETRY_OUTCOMES = frozenset(
    {
        "pending",
        "fanout_error",
        "eligible_without_post",
        "stale_reservation",
    }
)
RECHECK_OUTCOMES = frozenset(
    {
        "quality_blocked",
        "fanout_error",
        "pending",
        "invalid_snapshot",
        "exact_identity_blocked",
    }
)


@dataclass(frozen=True)
class WalmartRecoveryItem:
    deal_key: str
    public_key: str
    label: str
    event_at: str
    outcome: str
    detail: str
    discount: float | None
    threshold: int
    item_id: str
    product_url: str
    last_error: str
    post_status: str
    candidate: Any | None
    card: Any | None

    @property
    def can_retry_current_rules(self) -> bool:
        return self.card is not None and self.outcome in SAFE_RETRY_OUTCOMES

    @property
    def can_owner_override(self) -> bool:
        return self.card is not None and self.outcome in SOFT_OVERRIDE_OUTCOMES

    @property
    def can_recheck_exact(self) -> bool:
        return bool(self.item_id) and self.outcome in RECHECK_OUTCOMES

    @property
    def can_share_manual_lead(self) -> bool:
        return self.card is not None and self.outcome == "quality_blocked"

    def compact_reason(self) -> str:
        discount = "unknown markdown" if self.discount is None else f"{self.discount:.0f}% off"
        return f"{discount} • {self.detail}"


@dataclass(frozen=True)
class WalmartRecoveryActionResult:
    ok: bool
    message: str
    channel_id: int | None = None
    message_id: int | None = None


async def load_walmart_recovery_items(
    db: Any,
    *,
    guild_id: int,
    threshold: int,
    category_preferences: dict[str, str] | None = None,
    window_hours: int = RECOVERY_WINDOW_HOURS,
    event_limit: int = RECOVERY_EVENT_LIMIT,
) -> list[WalmartRecoveryItem]:
    """Load both fanout no-post events and hard exact-queue failures.

    The event table contains verified markdowns that reached fanout. Terminal
    seller/offer/item/proof failures never reach that table, so this loader also
    reads recent terminal exact-queue rows. The loader is read-only; every
    mutation is an explicit action from the private recovery console.
    """

    hours = max(1, min(24 * 30, int(window_hours)))
    limit = max(1, min(25, int(event_limit)))
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=hours)).isoformat()

    await legacy.ensure_global_deal_event_tables(db)
    await ensure_public_post_tables(db)
    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()

    event_cursor = await conn.execute(
        f"""
        SELECT deal_key, snapshot_json, first_seen_at, source_verified_at,
               processed_at, last_error
        FROM {legacy.EVENT_TABLE}
        WHERE first_seen_at >= ?
        ORDER BY first_seen_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    )
    event_rows = list(await event_cursor.fetchall())

    post_cursor = await conn.execute(
        """
        SELECT deal_key, status, first_seen_at
        FROM guild_public_deal_posts
        WHERE CAST(guild_id AS TEXT) = ?
          AND retailer = 'walmart'
          AND first_seen_at >= ?
        """,
        (snowflake_text(guild_id), cutoff),
    )
    post_rows = list(await post_cursor.fetchall())
    post_state = {
        str(_row_get(row, "deal_key", 0) or ""): (
            str(_row_get(row, "status", 1) or "").lower(),
            str(_row_get(row, "first_seen_at", 2) or ""),
        )
        for row in post_rows
    }

    queue_cursor = await conn.execute(
        f"""
        SELECT item_id, title, product_url, image_url, last_seen_at,
               status, last_error, snapshot_json
        FROM {QUEUE_TABLE}
        WHERE last_seen_at >= ?
          AND status IN ('incomplete_identity', 'identity_mismatch')
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    )
    queue_rows = list(await queue_cursor.fetchall())

    loaded: list[WalmartRecoveryItem] = []
    represented_item_ids: set[str] = set()

    for row in event_rows:
        deal_key = str(_row_get(row, "deal_key", 0) or "")
        snapshot_json = str(_row_get(row, "snapshot_json", 1) or "")
        event_at = str(
            _row_get(row, "source_verified_at", 3)
            or _row_get(row, "first_seen_at", 2)
            or "unknown"
        )
        processed_at = str(_row_get(row, "processed_at", 4) or "")
        last_error = str(_row_get(row, "last_error", 5) or "").strip()
        candidate = _candidate_from_snapshot(snapshot_json)
        item_id = _candidate_item_id(candidate)
        if item_id:
            represented_item_ids.add(item_id)
        card = legacy._exact_card_for_candidate(candidate)

        if card is None:
            loaded.append(
                WalmartRecoveryItem(
                    deal_key=deal_key,
                    public_key=deal_key,
                    label="Unreadable exact Walmart snapshot",
                    event_at=event_at,
                    outcome="invalid_snapshot",
                    detail="snapshot could not rebuild an exact public card",
                    discount=None,
                    threshold=int(threshold),
                    item_id=item_id,
                    product_url=_candidate_product_url(candidate),
                    last_error=last_error,
                    post_status="",
                    candidate=candidate,
                    card=None,
                )
            )
            continue

        label = str(getattr(card, "label", None) or "Walmart deal")
        discount = _float_or_none(structured_discount(card))
        category = decide_category(card, category_preferences or {})
        retailer = str(getattr(card, "retailer", None) or "walmart")
        public_key = str(
            getattr(card, "public_post_key", None)
            or card_deal_key(card, retailer=retailer)
        )
        status, reservation_at = (
            post_state.get(public_key)
            or post_state.get(deal_key)
            or ("", "")
        )

        if status == "posted":
            continue
        if not processed_at:
            outcome = "pending"
            detail = "waiting for global fanout"
        elif last_error:
            outcome = "fanout_error"
            detail = f"fanout error: {_compact(last_error, 180)}"
        elif status == "sending":
            outcome = "delivery_in_progress"
            detail = "normal delivery is actively sending; override is locked to prevent a race duplicate"
        elif status == "reserved":
            if _is_stale_reservation(reservation_at, now=now):
                outcome = "stale_reservation"
                detail = "a stale post reservation blocked delivery and can be retried or overridden once"
            else:
                outcome = "delivery_in_progress"
                detail = "normal delivery holds an active reservation; wait or retry after it becomes stale"
        elif category.action == "suppress":
            outcome = "category_muted"
            detail = f"muted category: {category.category_label}"
        elif discount is not None and discount < int(threshold):
            outcome = "below_threshold"
            detail = f"below this server's {int(threshold)}% threshold"
        elif not is_public_deal_candidate(
            card,
            source_label=SOURCE_LABEL,
            min_discount=int(threshold),
        ):
            outcome = "quality_blocked"
            detail = "blocked by exact proof/quality guard"
        else:
            outcome = "eligible_without_post"
            detail = "current rules allow it, but no durable post receipt exists"

        loaded.append(
            WalmartRecoveryItem(
                deal_key=deal_key,
                public_key=public_key,
                label=label,
                event_at=event_at,
                outcome=outcome,
                detail=detail,
                discount=discount,
                threshold=int(threshold),
                item_id=item_id,
                product_url=str(
                    getattr(card, "url", None)
                    or _candidate_product_url(candidate)
                    or ""
                ),
                last_error=last_error,
                post_status=status,
                candidate=candidate,
                card=card,
            )
        )

    for row in queue_rows:
        item_id = str(_row_get(row, "item_id", 0) or "").strip()
        if not item_id or item_id in represented_item_ids:
            continue
        title = str(_row_get(row, "title", 1) or f"Walmart item {item_id}")
        product_url = str(
            _row_get(row, "product_url", 2)
            or f"https://www.walmart.com/ip/{item_id}"
        )
        image_url = str(_row_get(row, "image_url", 3) or "")
        event_at = str(_row_get(row, "last_seen_at", 4) or "unknown")
        status = str(_row_get(row, "status", 5) or "")
        last_error = str(_row_get(row, "last_error", 6) or "").strip()
        snapshot_json = str(_row_get(row, "snapshot_json", 7) or "")
        candidate = _candidate_from_snapshot(snapshot_json)
        if candidate is None:
            candidate = _queue_review_candidate(
                item_id=item_id,
                title=title,
                product_url=product_url,
                image_url=image_url,
            )
        detail = (
            f"exact identity/proof blocked ({status}): "
            f"{_compact(last_error or 'the exact worker did not receive complete identity proof', 240)}"
        )
        loaded.append(
            WalmartRecoveryItem(
                deal_key=f"queue:{item_id}",
                public_key="",
                label=title,
                event_at=event_at,
                outcome="exact_identity_blocked",
                detail=detail,
                discount=None,
                threshold=int(threshold),
                item_id=item_id,
                product_url=product_url,
                last_error=last_error,
                post_status="",
                candidate=candidate,
                card=None,
            )
        )

    loaded.sort(key=lambda item: item.event_at, reverse=True)
    return loaded[:limit]


async def retry_walmart_delivery_current_rules(
    *,
    bot: Any,
    guild_id: int,
    item: WalmartRecoveryItem,
    actor_id: int,
) -> WalmartRecoveryActionResult:
    if not item.can_retry_current_rules or item.card is None:
        return WalmartRecoveryActionResult(
            False,
            "That item cannot use safe retry. Use its exact recheck or owner-review action instead.",
        )

    result = await maybe_post_public_deal_cards(
        bot=bot,
        guild_id=int(guild_id),
        cards=[item.card],
        source_label=SOURCE_LABEL,
        fallback_retailer="walmart",
        min_public_discount=int(item.threshold),
    )
    if result.posted:
        message = "Posted successfully using the server's current threshold, category, proof, and duplicate rules."
        ok = True
    elif result.skipped_recent_alert_duplicate or result.skipped_reserved_duplicate:
        message = "Safe retry was blocked by the normal duplicate/reservation guard. The server owner can use **Post once** after reviewing it when no normal send is active."
        ok = False
    elif result.skipped_not_alertable:
        message = "Safe retry still fails the current threshold, category, or exact-proof gate. The recovery panel keeps the real reason visible."
        ok = False
    elif result.skipped_disabled or result.skipped_wrong_retailer:
        message = "Safe retry could not use this server's current Walmart delivery configuration."
        ok = False
    else:
        notes = " | ".join(result.errors) if result.errors else "no send result was recorded"
        message = f"Safe retry did not post: `{_compact(notes, 500)}`"
        ok = False

    await _record_action(
        bot.db,
        guild_id=guild_id,
        deal_key=item.deal_key,
        actor_id=actor_id,
        action="retry_current_rules",
        outcome="posted" if ok else "blocked",
        detail=message,
    )
    return WalmartRecoveryActionResult(ok, message)


async def post_walmart_owner_override(
    *,
    bot: Any,
    guild_id: int,
    item: WalmartRecoveryItem,
    actor_id: int,
) -> WalmartRecoveryActionResult:
    """Bypass only one soft guild rule while retaining exact proof.

    Missing item/offer/seller/variant/structured-price proof can never use this
    path. A live normal reservation/sending state is also never bypassed because
    doing so could create two public posts at once.
    """

    if not item.can_owner_override or item.card is None:
        return WalmartRecoveryActionResult(
            False,
            "This reason is not eligible for a verified owner override. Recheck the exact offer or share an available review card as a clearly labeled manual lead.",
        )
    if item.post_status == "sending":
        return WalmartRecoveryActionResult(
            False,
            "Normal delivery is actively sending this event. Owner override is locked to prevent a race duplicate.",
        )
    if not is_public_deal_candidate(
        item.card,
        source_label=SOURCE_LABEL,
        min_discount=1,
    ):
        return WalmartRecoveryActionResult(
            False,
            "Owner override stopped: the item no longer passes exact item/offer/seller/variant and structured-price proof. It cannot be called a verified deal.",
        )

    db = getattr(bot, "db", None)
    if db is None:
        return WalmartRecoveryActionResult(False, "Bot database is unavailable.")
    config = await get_public_alert_config(db, int(guild_id))
    channel_id = config.get("channel_id")
    if not config.get("enabled") or not channel_id:
        return WalmartRecoveryActionResult(
            False,
            "No public deal channel is configured. Run `/setup_sniperplug_here` first.",
        )
    if "walmart" not in set(config.get("retailers") or ()):
        return WalmartRecoveryActionResult(
            False,
            "Walmart public delivery is disabled for this server.",
        )

    channel, channel_note = await resolve_public_alert_channel(
        bot,
        db,
        guild_id=int(guild_id),
        configured_channel_id=channel_id,
    )
    if channel is None:
        return WalmartRecoveryActionResult(
            False,
            channel_note or "Public deal channel could not be resolved.",
        )

    retailer = "walmart"
    override_key = f"{OWNER_OVERRIDE_POST_PREFIX}:{item.deal_key}"
    reserved = await reserve_public_deal_post(
        db,
        guild_id=int(guild_id),
        retailer=retailer,
        deal_key=override_key,
        source_label=OWNER_OVERRIDE_SOURCE_LABEL,
    )
    if not reserved:
        return WalmartRecoveryActionResult(
            False,
            "This exact event has already used its one-time owner override or is currently being sent.",
        )

    try:
        await mark_public_deal_sending(
            db,
            guild_id=int(guild_id),
            deal_key=override_key,
        )
        embed = _owner_override_embed(item, actor_id=actor_id)
        product_key = card_product_key(item.card, retailer=retailer)
        target = build_feedback_target(
            item.card,
            target_key=product_key,
            retailer=retailer,
            source_label=OWNER_OVERRIDE_SOURCE_LABEL,
        )
        feedback_view = await build_deal_feedback_view(
            db,
            guild_id=int(guild_id),
            target=target,
        )
        message = await channel.send(
            embed=sanitize_embed(embed),
            view=feedback_view,
        )
    except Exception as exc:
        await release_public_deal_reservation(
            db,
            guild_id=int(guild_id),
            deal_key=override_key,
        )
        detail = f"Owner override send failed: `{type(exc).__name__}: {_compact(exc, 260)}`"
        await _record_action(
            db,
            guild_id=guild_id,
            deal_key=item.deal_key,
            actor_id=actor_id,
            action="owner_override",
            outcome="error",
            detail=detail,
        )
        return WalmartRecoveryActionResult(False, detail)

    current_price = _float_or_none(
        getattr(item.card, "current_price", None)
        or getattr(item.card, "api_current_price", None)
    )
    finalized, notes = await finalize_successful_public_post(
        db,
        guild_id=int(guild_id),
        retailer=retailer,
        deal_key=override_key,
        product_key=product_key,
        alert_key=PUBLIC_ALERT_KEY,
        current_price=current_price,
        channel_id=getattr(channel, "id", channel_id),
        message_id=getattr(message, "id", None),
        allow_review_scout=False,
    )
    await _mark_original_delivery_receipt(
        db,
        guild_id=int(guild_id),
        deal_key=item.public_key or item.deal_key,
    )
    detail = (
        "Posted once as a server-owner override. Exact proof still passed; the automatic threshold/category settings were not changed."
    )
    if channel_note:
        detail += f" {channel_note}"
    if notes:
        detail += " Notes: " + " | ".join(notes)
    if not finalized:
        detail += " The message was sent, but durable override finalization needs inspection."

    await _record_action(
        db,
        guild_id=guild_id,
        deal_key=item.deal_key,
        actor_id=actor_id,
        action="owner_override",
        outcome="posted" if finalized else "posted_unconfirmed",
        detail=detail,
        channel_id=getattr(channel, "id", None),
        message_id=getattr(message, "id", None),
    )
    return WalmartRecoveryActionResult(
        True,
        detail,
        channel_id=_int_or_none(getattr(channel, "id", None)),
        message_id=_int_or_none(getattr(message, "id", None)),
    )


async def recheck_walmart_exact_offer(
    *,
    db: Any,
    guild_id: int,
    item: WalmartRecoveryItem,
    actor_id: int,
) -> WalmartRecoveryActionResult:
    if not item.can_recheck_exact or not item.item_id:
        return WalmartRecoveryActionResult(
            False,
            "This event does not contain a usable Walmart item ID for exact recheck.",
        )

    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = 'pending',
            next_attempt_at = ?,
            last_seen_at = ?,
            lease_token = '',
            lease_until = NULL,
            last_error = CASE
                WHEN last_error = '' THEN ?
                ELSE last_error || ' | ' || ?
            END
        WHERE item_id = ?
        """,
        (
            now,
            now,
            OWNER_RECHECK_SOURCE_LABEL,
            OWNER_RECHECK_SOURCE_LABEL,
            item.item_id,
        ),
    )
    await conn.commit()
    verify = await conn.execute(
        f"SELECT status, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ? LIMIT 1",
        (item.item_id,),
    )
    row = await verify.fetchone()
    if row is None or str(_row_get(row, "status", 0) or "") != "pending":
        return WalmartRecoveryActionResult(
            False,
            "Exact recheck could not find or re-arm the queue row.",
        )

    detail = (
        f"Walmart item `{item.item_id}` was re-armed for the next exact-detail worker cycle. "
        "It still must pass item, offer, seller, variant, availability, and price proof before verified posting."
    )
    await _record_action(
        db,
        guild_id=guild_id,
        deal_key=item.deal_key,
        actor_id=actor_id,
        action="recheck_exact",
        outcome="queued",
        detail=detail,
    )
    return WalmartRecoveryActionResult(True, detail)


async def share_walmart_manual_lead(
    *,
    bot: Any,
    guild_id: int,
    item: WalmartRecoveryItem,
    actor_id: int,
) -> WalmartRecoveryActionResult:
    if not item.can_share_manual_lead or item.card is None:
        return WalmartRecoveryActionResult(
            False,
            "This item does not have a reviewable card for manual lead sharing. Use **Open Walmart** after an exact-identity block, or request an exact recheck.",
        )
    ok, message = await share_review_card(
        bot=bot,
        guild_id=int(guild_id),
        card=item.card,
        fallback_retailer="walmart",
    )
    await _record_action(
        bot.db,
        guild_id=guild_id,
        deal_key=item.deal_key,
        actor_id=actor_id,
        action="share_manual_lead",
        outcome="posted" if ok else "blocked",
        detail=message,
    )
    return WalmartRecoveryActionResult(ok, message)


async def ensure_walmart_recovery_action_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RECOVERY_ACTION_TABLE} (
            action_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            deal_key TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            channel_id TEXT,
            message_id TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{RECOVERY_ACTION_TABLE}_guild_created "
        f"ON {RECOVERY_ACTION_TABLE} (guild_id, created_at DESC)"
    )
    await conn.commit()


async def _record_action(
    db: Any,
    *,
    guild_id: int,
    deal_key: str,
    actor_id: int,
    action: str,
    outcome: str,
    detail: str,
    channel_id: Any = None,
    message_id: Any = None,
) -> None:
    try:
        await ensure_walmart_recovery_action_table(db)
        conn = db.require_conn()
        await conn.execute(
            f"""
            INSERT INTO {RECOVERY_ACTION_TABLE} (
                guild_id, deal_key, actor_id, action, outcome, detail,
                channel_id, message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snowflake_text(guild_id),
                str(deal_key),
                snowflake_text(actor_id),
                str(action),
                str(outcome),
                _compact(detail, 1000),
                snowflake_text(channel_id) if channel_id not in (None, "") else None,
                snowflake_text(message_id) if message_id not in (None, "") else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await conn.commit()
    except Exception:
        # Recovery audit failure must not falsely report the user action failed.
        return


async def _mark_original_delivery_receipt(
    db: Any,
    *,
    guild_id: int,
    deal_key: str,
) -> None:
    if not deal_key:
        return
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """
        INSERT INTO guild_public_deal_posts (
            guild_id, deal_key, retailer, source_label,
            status, first_seen_at, posted_at
        ) VALUES (?, ?, 'walmart', ?, 'posted', ?, ?)
        ON CONFLICT(guild_id, deal_key) DO UPDATE SET
            retailer = 'walmart',
            source_label = excluded.source_label,
            status = 'posted',
            posted_at = excluded.posted_at
        """,
        (
            snowflake_text(guild_id),
            str(deal_key),
            OWNER_OVERRIDE_SOURCE_LABEL,
            now,
            now,
        ),
    )
    await conn.commit()


def _owner_override_embed(
    item: WalmartRecoveryItem,
    *,
    actor_id: int,
) -> discord.Embed:
    source = getattr(item.card, "embed", None)
    if isinstance(source, discord.Embed):
        embed = discord.Embed.from_dict(source.to_dict())
    else:
        embed = discord.Embed(
            title=item.label,
            url=item.product_url or None,
            color=discord.Color.orange(),
        )
    value = (
        f"Manually posted once by <@{int(actor_id)}> despite **{item.detail}**. "
        "Exact item, offer, seller, variant, availability, and structured price proof still passed. "
        "Automatic threshold and category settings were not changed."
    )
    if len(embed.fields) < 25:
        embed.add_field(
            name="🛠️ Server owner override",
            value=value[:1024],
            inline=False,
        )
    else:
        embed.description = _compact(
            f"{embed.description or ''}\n\n🛠️ **Server owner override:** {value}",
            4096,
        )
    original_footer = str(getattr(getattr(embed, "footer", None), "text", "") or "")
    footer = "Exact Walmart offer • one-time server-owner override"
    if original_footer:
        footer = f"{original_footer} • {footer}"
    embed.set_footer(text=_compact(footer, 2048))
    return embed


def _queue_review_candidate(
    *,
    item_id: str,
    title: str,
    product_url: str,
    image_url: str,
) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart_exact_recovery_queue",
        retailer="Walmart",
        title=title,
        product_url=product_url,
        direct_product_url=product_url,
        image_url=image_url or None,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        variant_attributes={
            "recoverySource": "terminal_exact_queue",
            "exactIdentityVerified": "no",
        },
    )


def _candidate_item_id(candidate: Any | None) -> str:
    if candidate is None:
        return ""
    for value in (
        getattr(candidate, "product_id", None),
        getattr(candidate, "sku", None),
    ):
        text = str(value or "").strip()
        if text.isdigit():
            return text
    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    for key in ("exactDetailItemId", "itemId", "usItemId"):
        text = str(attrs.get(key) or "").strip()
        if text.isdigit():
            return text
    return ""


def _candidate_product_url(candidate: Any | None) -> str:
    if candidate is None:
        return ""
    return str(
        getattr(candidate, "direct_product_url", None)
        or getattr(candidate, "product_url", None)
        or ""
    )


def _is_stale_reservation(value: str, *, now: datetime) -> bool:
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    return parsed <= now - timedelta(minutes=RESERVATION_STALE_MINUTES)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError, OverflowError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, int(limit) - 1)].rstrip() + "…"
