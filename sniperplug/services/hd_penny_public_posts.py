"""Public deals-channel posts for Home Depot penny-tracker cards.

Modeled on :mod:`sniperplug.services.hp_public_posts`: same guild public
alert config (the server must have Home Depot among its picked stores),
same category preferences, same durable duplicate reservations, same
recent-alert suppression. The proof gate differs because the events
differ:

- A **penny** card posts when its structured price is exactly $0.01 with
  tracker proof attached. No discount threshold applies — the penny
  price itself is the proof.
- A **final-stage** card posts only when the ending is .03/.02 *and* the
  reference-backed markdown clears a floor. The scan-data research is
  blunt that a bare .03 ending on a shallow discount is usually
  coincidence, so the public gate demands real markdown depth (at least
  40% off, or the server's own higher starting threshold).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sniperplug.services.deal_feedback import build_deal_feedback_view, build_feedback_target
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import (
    PUBLIC_ALERT_KEY,
    PublicPostResult,
    card_deal_key,
    card_product_key,
    clean_error_text,
    ensure_public_post_tables,
    finalize_successful_public_post,
    mark_public_deal_sending,
    release_public_deal_reservation,
    reserve_public_deal_post,
    resolve_public_alert_channel,
    safe_find_recent_alert,
    should_suppress_recent_alert,
)
from sniperplug.services.public_posting import normalize_retailer_key


FINAL_STAGE_MIN_PUBLIC_DISCOUNT = 40


async def maybe_post_hd_penny_cards(
    *,
    bot: Any,
    guild_id: int | None,
    cards: list[Any],
    source_label: str = "home_depot_penny_tracker:observed",
    min_public_discount: int = 10,
) -> PublicPostResult:
    if guild_id is None or not cards:
        return PublicPostResult()
    db = getattr(bot, "db", None)
    if db is None:
        return PublicPostResult(
            attempted=len(cards),
            errors=("Home Depot penny public posting skipped: bot database unavailable",),
        )

    config = await get_public_alert_config(db, int(guild_id))
    if not config.get("enabled") or not config.get("channel_id"):
        return PublicPostResult(attempted=len(cards), skipped_disabled=len(cards))
    allowed = {normalize_retailer_key(value) for value in config.get("retailers") or ()}
    if "home_depot" not in allowed:
        return PublicPostResult(attempted=len(cards), skipped_wrong_retailer=len(cards))

    try:
        from sniperplug.services.deal_category_preferences import get_category_preferences

        category_preferences = await get_category_preferences(db, int(guild_id))
    except Exception as error:
        return PublicPostResult(
            attempted=len(cards),
            errors=(f"Home Depot category preference read failed; posting blocked: {clean_error_text(error)}",),
        )

    channel, channel_note = await resolve_public_alert_channel(
        bot,
        db,
        guild_id=int(guild_id),
        configured_channel_id=config["channel_id"],
    )
    if channel is None:
        return PublicPostResult(
            attempted=len(cards),
            errors=(channel_note or "Home Depot penny public channel lookup failed",),
        )

    posted = duplicate = not_alertable = 0
    notes: list[str] = [channel_note] if channel_note else []
    for card in cards:
        try:
            from sniperplug.services.deal_category_preferences import decide_category

            if decide_category(card, category_preferences).action == "suppress":
                not_alertable += 1
                continue
        except Exception as error:
            not_alertable += 1
            notes.append(f"Home Depot category decision failed; card blocked: {clean_error_text(error)}")
            continue

        if not is_verified_hd_penny_public_card(card, min_discount=min_public_discount):
            not_alertable += 1
            continue

        retailer = "home_depot"
        current_price = _positive_float(getattr(card, "current_price", None))
        product_key = card_product_key(card, retailer=retailer)
        recent = await safe_find_recent_alert(
            db,
            guild_id=int(guild_id),
            retailer=retailer,
            product_key=product_key,
            current_price=current_price,
            alert_key=PUBLIC_ALERT_KEY,
            errors=notes,
        )
        if recent and should_suppress_recent_alert(recent, current_price):
            duplicate += 1
            continue

        deal_key = getattr(card, "public_post_key", None) or card_deal_key(card, retailer=retailer)
        reserved = await reserve_public_deal_post(
            db,
            guild_id=int(guild_id),
            retailer=retailer,
            deal_key=deal_key,
            source_label=source_label,
        )
        if not reserved:
            duplicate += 1
            continue

        try:
            await mark_public_deal_sending(db, guild_id=int(guild_id), deal_key=deal_key)
            target = build_feedback_target(
                card,
                target_key=product_key,
                retailer=retailer,
                source_label=source_label,
            )
            view = await build_deal_feedback_view(db, guild_id=int(guild_id), target=target)
            message = await channel.send(embed=sanitize_embed(card.embed), view=view)
        except Exception as error:
            await release_public_deal_reservation(db, guild_id=int(guild_id), deal_key=deal_key)
            notes.append(f"Home Depot penny public post failed: {clean_error_text(error)}")
            continue

        finalized, finalize_notes = await finalize_successful_public_post(
            db,
            guild_id=int(guild_id),
            retailer=retailer,
            deal_key=deal_key,
            product_key=product_key,
            alert_key=PUBLIC_ALERT_KEY,
            current_price=current_price,
            channel_id=getattr(channel, "id", config["channel_id"]),
            message_id=getattr(message, "id", None),
            allow_review_scout=False,
        )
        notes.extend(finalize_notes)
        if not finalized:
            notes.append("Home Depot penny post sent but durable duplicate state could not be fully confirmed")
        try:
            await _cache_hd_active_deal(
                db,
                guild_id=int(guild_id),
                card=card,
                source_label=source_label,
            )
        except Exception as error:
            notes.append(f"Home Depot active deal cache write failed: {clean_error_text(error)}")
        posted += 1

    return PublicPostResult(
        attempted=len(cards),
        posted=posted,
        skipped_duplicate=duplicate,
        skipped_recent_alert_duplicate=duplicate,
        skipped_not_alertable=not_alertable,
        errors=tuple(note for note in notes if note)[:8],
    )


def is_verified_hd_penny_public_card(card: Any, *, min_discount: int) -> bool:
    retailer = normalize_retailer_key(getattr(card, "retailer", None))
    if retailer != "home_depot":
        return False
    url = str(getattr(card, "direct_product_url", None) or getattr(card, "url", "") or "").strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not (parsed.hostname or "").lower().endswith("homedepot.com"):
        return False
    if "/p/" not in parsed.path.lower():
        return False

    attrs = dict(getattr(card, "variant_attributes", None) or {})
    if str(attrs.get("hdPennyProof") or "").lower() != "yes":
        return False

    current = _positive_float(
        getattr(card, "api_current_price", None) or getattr(card, "current_price", None)
    )
    if current is None:
        return False
    stage = str(getattr(card, "hd_penny_stage", None) or attrs.get("hdPennyStage") or "")

    if stage == "penny" or abs(current - 0.01) < 0.005:
        # The penny price is the proof; no discount threshold applies.
        return abs(current - 0.01) < 0.005

    if stage != "final":
        return False
    cents = int(round(current * 100))
    if cents % 100 not in (2, 3):
        return False
    reference = _positive_float(
        getattr(card, "api_reference_price", None) or getattr(card, "typical_price", None)
    )
    if reference is None or reference <= current:
        return False
    discount = (reference - current) / reference * 100.0
    return discount >= max(FINAL_STAGE_MIN_PUBLIC_DISCOUNT, int(min_discount))


async def _cache_hd_active_deal(db: Any, *, guild_id: int, card: Any, source_label: str) -> None:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    active_key = card_product_key(card, retailer="home_depot")
    await conn.execute(
        """
        INSERT INTO guild_active_deal_cache (
            guild_id, active_key, retailer, title, url, current_price,
            discount, score, source_label, status, first_seen_at, last_seen_at
        ) VALUES (?, ?, 'home_depot', ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        ON CONFLICT(guild_id, active_key) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            current_price = excluded.current_price,
            discount = excluded.discount,
            score = excluded.score,
            source_label = excluded.source_label,
            status = 'active',
            last_seen_at = excluded.last_seen_at
        """,
        (
            guild_id,
            active_key,
            getattr(card, "label", None) or "Home Depot penny watch",
            getattr(card, "url", ""),
            getattr(card, "current_price", None),
            getattr(card, "discount", None),
            getattr(card, "score", None),
            source_label,
            now,
            now,
        ),
    )
    await conn.commit()


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
