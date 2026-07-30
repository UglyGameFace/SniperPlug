from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.deal_feedback import build_deal_feedback_view, build_feedback_target
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.public_alert_config import get_public_alert_config, set_public_alert_channel_id
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.public_deal_quality import is_public_deal_candidate, prepare_public_deal_candidate, prepare_public_scout_candidate


ALERT_DEDUPE_DAYS = 30
SCOUT_ALERT_DEDUPE_HOURS = 6
PUBLIC_ALERT_KEY = "public_alert:v1"
PUBLIC_SCOUT_ALERT_KEY = "public_scout_alert:v1"
PUBLIC_CHANNEL_NAME_FALLBACKS = ("walmart-deals", "deals", "deal-alerts", "sniperplug-deals")
RESERVATION_STALE_MINUTES = 20
_MISSING_ROWCOUNT = object()


@dataclass(frozen=True)
class PublicPostResult:
    attempted: int = 0
    posted: int = 0
    skipped_duplicate: int = 0
    skipped_recent_alert_duplicate: int = 0
    skipped_reserved_duplicate: int = 0
    skipped_not_alertable: int = 0
    skipped_disabled: int = 0
    skipped_wrong_retailer: int = 0
    cached_active: int = 0
    errors: tuple[str, ...] = ()

    @property
    def any_activity(self) -> bool:
        return bool(
            self.attempted
            or self.posted
            or self.skipped_duplicate
            or self.skipped_recent_alert_duplicate
            or self.skipped_reserved_duplicate
            or self.skipped_not_alertable
            or self.skipped_disabled
            or self.skipped_wrong_retailer
            or self.cached_active
            or self.errors
        )


async def maybe_post_public_deal_cards(
    *,
    bot: Any,
    guild_id: int | None,
    cards: list[Any],
    source_label: str,
    fallback_retailer: str | None = None,
    min_alert_score: int = 90,
    min_public_discount: int = 50,
    allow_review_scout: bool = False,
) -> PublicPostResult:
    if guild_id is None or not cards:
        return PublicPostResult()

    attempted = len(cards)
    db = getattr(bot, "db", None)
    if db is None:
        return PublicPostResult(attempted=attempted, errors=("public posting skipped: bot database unavailable",))

    fallback_key = normalize_retailer_key(fallback_retailer)
    try:
        config = await get_public_alert_config(db, guild_id)
    except Exception as exc:
        return PublicPostResult(
            attempted=attempted,
            errors=(f"public alert configuration read failed: {clean_error_text(exc)}",),
        )
    if not config["enabled"] or not config["channel_id"]:
        return PublicPostResult(attempted=attempted, skipped_disabled=attempted)

    try:
        from sniperplug.services.deal_category_preferences import get_category_preferences

        category_preferences = await get_category_preferences(db, guild_id)
    except Exception as exc:
        return PublicPostResult(
            attempted=attempted,
            errors=(f"public category preference read failed; posting blocked: {clean_error_text(exc)}",),
        )

    channel, channel_note = await resolve_public_alert_channel(
        bot,
        db,
        guild_id=guild_id,
        configured_channel_id=config["channel_id"],
    )
    if channel is None:
        return PublicPostResult(attempted=attempted, errors=(channel_note or "public channel lookup failed",))
    if not hasattr(channel, "send"):
        return PublicPostResult(
            attempted=attempted,
            errors=(f"configured public alert channel <#{getattr(channel, 'id', config['channel_id'])}> is not sendable",),
        )

    allowed_retailers = set(config["retailers"])
    posted = 0
    skipped_recent_alert_duplicate = 0
    skipped_reserved_duplicate = 0
    skipped_not_alertable = 0
    skipped_wrong_retailer = 0
    notes: list[str] = []
    cache_after_posting: list[Any] = []
    if channel_note:
        notes.append(channel_note)

    for card in cards:
        try:
            from sniperplug.services.deal_category_preferences import decide_category

            category_decision = decide_category(card, category_preferences)
        except Exception as exc:
            notes.append(f"public category decision failed; card blocked: {clean_error_text(exc)}")
            skipped_not_alertable += 1
            continue
        if category_decision.action == "suppress":
            skipped_not_alertable += 1
            continue

        retailer = normalize_retailer_key(getattr(card, "retailer", None)) or fallback_key
        if retailer not in allowed_retailers:
            skipped_wrong_retailer += 1
            continue

        if allow_review_scout:
            public_ready = prepare_public_scout_candidate(card, source_label=source_label)
        else:
            public_ready = prepare_public_deal_candidate(
                card,
                source_label=source_label,
                min_discount=min_public_discount,
            )
        if not public_ready:
            skipped_not_alertable += 1
            continue

        should_alert = getattr(card, "should_alert", None)
        if should_alert is None:
            should_alert = int(getattr(card, "score", 0) or 0) >= min_alert_score
        if not bool(should_alert):
            skipped_not_alertable += 1
            continue

        current_price = _float_or_none(getattr(card, "current_price", None))
        product_key = card_product_key(card, retailer=retailer)
        alert_key = PUBLIC_SCOUT_ALERT_KEY if allow_review_scout else PUBLIC_ALERT_KEY
        recent_alert = await safe_find_recent_alert(
            db,
            guild_id=guild_id,
            retailer=retailer,
            product_key=product_key,
            current_price=current_price,
            alert_key=alert_key,
            errors=notes,
        )
        if recent_alert and should_suppress_recent_alert(recent_alert, current_price):
            skipped_recent_alert_duplicate += 1
            continue

        deal_key = getattr(card, "public_post_key", None) or card_deal_key(card, retailer=retailer)
        try:
            reserved = await reserve_public_deal_post(
                db,
                guild_id=guild_id,
                retailer=retailer,
                deal_key=deal_key,
                source_label=source_label,
            )
        except Exception as exc:
            notes.append(f"public post reservation failed for {retailer}: {clean_error_text(exc)}")
            continue
        if not reserved:
            skipped_reserved_duplicate += 1
            continue

        try:
            target = build_feedback_target(
                card,
                target_key=product_key,
                retailer=retailer,
                source_label=source_label,
            )
            feedback_view = await build_deal_feedback_view(db, guild_id=guild_id, target=target)
            message = await channel.send(embed=sanitize_embed(card.embed), view=feedback_view)
        except Exception as exc:  # pragma: no cover
            try:
                await release_public_deal_reservation(db, guild_id=guild_id, deal_key=deal_key)
            except Exception as release_exc:
                notes.append(f"reservation cleanup failed for {retailer}: {clean_error_text(release_exc)}")
            notes.append(
                f"public post failed for {retailer} in <#{getattr(channel, 'id', config['channel_id'])}>: "
                f"{clean_error_text(exc)}"
            )
            continue

        try:
            await mark_public_deal_posted(db, guild_id=guild_id, deal_key=deal_key)
        except Exception as exc:
            notes.append(f"public post state write failed for {retailer}: {clean_error_text(exc)}")

        try:
            await db.record_alert_dedupe(
                guild_id=guild_id,
                retailer=retailer,
                product_key=product_key,
                alert_key=alert_key,
                current_price=current_price,
                channel_id=getattr(channel, "id", config["channel_id"]),
                message_id=getattr(message, "id", None),
                threshold_price=current_price,
                expires_at=alert_expires_at(hours=SCOUT_ALERT_DEDUPE_HOURS if allow_review_scout else None),
            )
        except Exception as exc:
            notes.append(f"alert dedupe write failed for {retailer}: {clean_error_text(exc)}")
        if not allow_review_scout:
            cache_after_posting.append(card)
        posted += 1

    cached_active = 0
    if cache_after_posting:
        try:
            cached_active = await cache_active_deal_cards(
                db,
                guild_id=guild_id,
                cards=cache_after_posting,
                source_label=source_label,
                fallback_retailer=fallback_key,
                min_discount=min_public_discount,
            )
        except Exception as exc:
            notes.append(f"active deal cache write failed: {clean_error_text(exc)}")

    skipped_duplicate = skipped_recent_alert_duplicate + skipped_reserved_duplicate
    return PublicPostResult(
        attempted=attempted,
        posted=posted,
        skipped_duplicate=skipped_duplicate,
        skipped_recent_alert_duplicate=skipped_recent_alert_duplicate,
        skipped_reserved_duplicate=skipped_reserved_duplicate,
        skipped_not_alertable=skipped_not_alertable,
        skipped_disabled=0,
        skipped_wrong_retailer=skipped_wrong_retailer,
        cached_active=cached_active,
        errors=tuple(notes[:8]),
    )


async def resolve_public_alert_channel(
    bot: Any,
    db: Any,
    *,
    guild_id: int,
    configured_channel_id: int | str,
) -> tuple[Any | None, str | None]:
    channel_id = decode_channel_id(configured_channel_id)
    if channel_id is None:
        return None, f"stored public alert channel id is invalid: `{configured_channel_id}`"

    guild = bot.get_guild(guild_id)
    if guild is None:
        try:
            fetched = await bot.fetch_channel(channel_id)
            fetched_guild_id = getattr(getattr(fetched, "guild", None), "id", None)
            if fetched_guild_id is not None and int(fetched_guild_id) != int(guild_id):
                return None, (
                    f"public channel lookup failed: saved route uses ghost guild `{guild_id}`, "
                    f"but saved channel <#{channel_id}> belongs to live guild `{fetched_guild_id}`. "
                    "Run `/setup_sniperplug_here` inside the live public deals channel to repair the route."
                )
        except Exception:
            pass
        return None, f"public channel lookup failed: bot is not currently connected to guild `{guild_id}`"

    channel = guild.get_channel(channel_id)
    if channel is not None:
        permission_error = public_channel_permission_error(guild, channel)
        return (None, permission_error) if permission_error else (channel, None)

    fetch_error: str | None = None
    try:
        fetched = await bot.fetch_channel(channel_id)
        fetched_guild_id = getattr(getattr(fetched, "guild", None), "id", None)
        if fetched_guild_id is not None and int(fetched_guild_id) != int(guild_id):
            fetch_error = (
                f"stored public alert channel <#{channel_id}> belongs to another guild "
                f"(`{fetched_guild_id}`), not this one (`{guild_id}`)"
            )
        else:
            permission_error = public_channel_permission_error(guild, fetched)
            return (None, permission_error) if permission_error else (fetched, None)
    except Exception as exc:  # pragma: no cover
        fetch_error = f"stored public alert channel <#{channel_id}> could not be fetched: {clean_error_text(exc)}"

    repaired = await find_named_public_channel(guild)
    if repaired is None:
        return None, f"{fetch_error}. Run `/setup_sniperplug_here` inside the live public deals channel to save the correct route."

    permission_error = public_channel_permission_error(guild, repaired)
    if permission_error:
        return None, permission_error

    try:
        await set_public_alert_channel_id(db, guild_id=guild_id, channel_id=repaired.id)
    except Exception as exc:
        return None, f"found replacement public channel <#{repaired.id}> but failed to save repaired route: {clean_error_text(exc)}"
    return repaired, f"Public alert channel auto-repaired from stale <#{channel_id}> to live <#{repaired.id}> (`#{repaired.name}`)."


async def find_named_public_channel(guild: Any) -> Any | None:
    text_channels = list(getattr(guild, "text_channels", []) or [])
    for name in PUBLIC_CHANNEL_NAME_FALLBACKS:
        for channel in text_channels:
            if getattr(channel, "name", "") == name:
                return channel
    return None


def public_channel_permission_error(guild: Any, channel: Any) -> str | None:
    if not hasattr(channel, "send"):
        return f"configured public alert channel <#{getattr(channel, 'id', 'unknown')}> is not a sendable text channel"
    me = getattr(guild, "me", None)
    if me is None or not hasattr(channel, "permissions_for"):
        return None
    perms = channel.permissions_for(me)
    missing: list[str] = []
    if not getattr(perms, "view_channel", True):
        missing.append("View Channel")
    if not getattr(perms, "send_messages", True):
        missing.append("Send Messages")
    if not getattr(perms, "embed_links", True):
        missing.append("Embed Links")
    if missing:
        return f"bot is missing {', '.join(missing)} in <#{getattr(channel, 'id', 'unknown')}>"
    return None


def card_deal_key(card: Any, *, retailer: str) -> str:
    return public_post_key(
        retailer=retailer,
        url=getattr(card, "url", ""),
        current_price=getattr(card, "current_price", None),
        selected_offer_id=getattr(card, "selected_offer_id", None),
        sku=getattr(card, "sku", None),
        upc=getattr(card, "upc", None),
        score=getattr(card, "score", None),
        discount=getattr(card, "discount", None),
    )


def card_product_key(card: Any, *, retailer: str) -> str:
    return active_cache_key(
        retailer=retailer,
        url=getattr(card, "url", ""),
        selected_offer_id=getattr(card, "selected_offer_id", None),
        sku=getattr(card, "sku", None),
        upc=getattr(card, "upc", None),
    )


def public_post_key(
    *,
    retailer: str,
    url: str,
    current_price: float | None = None,
    selected_offer_id: str | None = None,
    sku: str | None = None,
    upc: str | None = None,
    score: int | None = None,
    discount: float | None = None,
) -> str:
    identity = selected_offer_id or sku or upc or canonical_url_key(url)
    if current_price is not None:
        price_part = price_key(current_price)
    elif discount is not None:
        price_part = f"discount:{float(discount):.2f}:score:{int(score or 0)}"
    else:
        price_part = "price:unknown"
    return ":".join((normalize_retailer_key(retailer), identity, price_part))


def active_cache_key(
    *,
    retailer: str,
    url: str,
    selected_offer_id: str | None = None,
    sku: str | None = None,
    upc: str | None = None,
) -> str:
    return ":".join((normalize_retailer_key(retailer), selected_offer_id or sku or upc or canonical_url_key(url)))


def price_key(value: float | None) -> str:
    if value is None:
        return "price:unknown"
    return f"price:{value:.2f}"


def canonical_url_key(url: str) -> str:
    text = (url or "").strip()
    return text.split("?", 1)[0].rstrip("/") or "unknown"


async def safe_find_recent_alert(
    db,
    *,
    guild_id: int,
    retailer: str,
    product_key: str,
    current_price: float | None,
    alert_key: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any] | None:
    try:
        return await db.find_recent_alert(
            guild_id=guild_id,
            retailer=retailer,
            product_key=product_key,
            current_price=current_price,
            alert_key=alert_key,
        )
    except Exception as exc:
        if errors is not None:
            errors.append(f"alert dedupe read failed for {retailer}: {clean_error_text(exc)}")
        return None


def should_suppress_recent_alert(recent_alert: dict[str, Any], current_price: float | None) -> bool:
    if not recent_alert:
        return False
    previous_price = _float_or_none(recent_alert.get("current_price"))
    if current_price is None or previous_price is None:
        return True
    return current_price >= previous_price


def alert_expires_at(days: int = ALERT_DEDUPE_DAYS, *, hours: int | None = None) -> str:
    if hours is not None:
        return (datetime.now(timezone.utc) + timedelta(hours=max(1, int(hours)))).isoformat()
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def cache_active_deal_cards(
    db,
    *,
    guild_id: int,
    cards: list[Any],
    source_label: str,
    fallback_retailer: str | None = None,
    min_discount: int = 50,
) -> int:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    cached = 0
    for card in cards:
        retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
        if not retailer:
            continue
        if not is_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
            continue
        url = getattr(card, "url", "") or ""
        key = active_cache_key(
            retailer=retailer,
            url=url,
            selected_offer_id=getattr(card, "selected_offer_id", None),
            sku=getattr(card, "sku", None),
            upc=getattr(card, "upc", None),
        )
        await conn.execute(
            """
            INSERT INTO guild_active_deal_cache (
                guild_id, active_key, retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
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
                key,
                retailer,
                getattr(card, "label", None) or "deal",
                url,
                getattr(card, "current_price", None),
                getattr(card, "discount", None),
                getattr(card, "score", None),
                source_label,
                now,
                now,
            ),
        )
        cached += 1
    await conn.commit()
    return cached


async def ensure_public_post_tables(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_deal_posts (
            guild_id INTEGER NOT NULL,
            deal_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            source_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved',
            first_seen_at TEXT NOT NULL,
            posted_at TEXT,
            PRIMARY KEY (guild_id, deal_key)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_active_deal_cache (
            guild_id INTEGER NOT NULL,
            active_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            current_price REAL,
            discount REAL,
            score INTEGER,
            source_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, active_key)
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_public_deal_posts_guild_retailer ON guild_public_deal_posts (guild_id, retailer)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_public_deal_posts_status_seen ON guild_public_deal_posts (guild_id, status, first_seen_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_public_deal_posts_posted ON guild_public_deal_posts (guild_id, status, posted_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_active_deal_cache_guild_retailer ON guild_active_deal_cache (guild_id, retailer, status)")
    await conn.commit()


async def reserve_public_deal_post(
    db,
    *,
    guild_id: int,
    retailer: str,
    deal_key: str,
    source_label: str,
) -> bool:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    stale_before = (now_dt - timedelta(minutes=RESERVATION_STALE_MINUTES)).isoformat()
    scout_stale_before = (now_dt - timedelta(hours=SCOUT_ALERT_DEDUPE_HOURS)).isoformat()

    if str(deal_key).startswith("scout:") or "scout" in str(source_label).lower():
        await conn.execute(
            """
            DELETE FROM guild_public_deal_posts
            WHERE guild_id = ?
              AND deal_key = ?
              AND status = 'posted'
              AND COALESCE(posted_at, first_seen_at) < ?
            """,
            (guild_id, deal_key, scout_stale_before),
        )

    await conn.execute(
        "DELETE FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ? AND status = 'reserved' AND first_seen_at < ?",
        (guild_id, deal_key, stale_before),
    )
    cursor = await conn.execute(
        """
        INSERT OR IGNORE INTO guild_public_deal_posts (guild_id, deal_key, retailer, source_label, status, first_seen_at)
        VALUES (?, ?, ?, ?, 'reserved', ?)
        """,
        (guild_id, deal_key, normalize_retailer_key(retailer), source_label, now),
    )
    await conn.commit()

    rowcount = getattr(cursor, "rowcount", _MISSING_ROWCOUNT)
    if rowcount is not _MISSING_ROWCOUNT:
        try:
            return int(rowcount) > 0
        except (TypeError, ValueError):
            pass

    check = await conn.execute(
        "SELECT 1 FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ? AND status = 'reserved' AND first_seen_at = ? LIMIT 1",
        (guild_id, deal_key, now),
    )
    return bool(await check.fetchone())


async def mark_public_deal_posted(db, *, guild_id: int, deal_key: str) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE guild_public_deal_posts SET status = 'posted', posted_at = ? WHERE guild_id = ? AND deal_key = ?",
        (now, guild_id, deal_key),
    )
    await conn.commit()


async def release_public_deal_reservation(db, *, guild_id: int, deal_key: str) -> None:
    conn = db.require_conn()
    await conn.execute(
        "DELETE FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ? AND status = 'reserved'",
        (guild_id, deal_key),
    )
    await conn.commit()


def decode_channel_id(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.startswith("ch:"):
        text = text[3:]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def clean_error_text(exc: BaseException | object, *, limit: int = 180) -> str:
    text = str(exc or "error")
    cleaned = "".join(ch if (ch.isprintable() and (ch == "\n" or ch == "\t" or ord(ch) >= 32)) else " " for ch in text)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].rstrip() + ("…" if len(cleaned) > limit else "")
