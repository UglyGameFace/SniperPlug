from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.public_deal_posts import (
    RESERVATION_STALE_MINUTES,
    active_cache_key,
    card_deal_key,
    card_product_key,
    ensure_public_post_tables,
    safe_find_recent_alert,
    should_suppress_recent_alert,
)
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.public_deal_quality import is_public_deal_candidate, prepare_public_deal_candidate
from sniperplug.services.public_quality_diagnostics import public_quality_block_reason


PREFLIGHT_REASON_ATTR = "autoscan_preflight_reason"


@dataclass(frozen=True)
class FreshDealSelection:
    fresh: list[Any]
    repeated_same_or_higher_price: int = 0
    lower_price_repeats: int = 0
    unknown_price_repeats: int = 0
    recent_public_duplicates: int = 0
    exact_public_post_duplicates: int = 0
    stale_reserved_recovered: int = 0
    not_alertable: int = 0

    @property
    def skipped_repeats(self) -> int:
        return self.repeated_same_or_higher_price + self.unknown_price_repeats

    def summary_line(self) -> str:
        parts = [f"fresh/new public-ready: **{len(self.fresh)}**"]
        if self.recent_public_duplicates:
            parts.append(f"recent public alert duplicates hidden: **{self.recent_public_duplicates}**")
        if self.exact_public_post_duplicates:
            parts.append(f"exact public post duplicates hidden: **{self.exact_public_post_duplicates}**")
        if self.stale_reserved_recovered:
            parts.append(f"stale reserved rows ignored: **{self.stale_reserved_recovered}**")
        if self.not_alertable:
            parts.append(f"not public-alertable hidden: **{self.not_alertable}**")
        if self.repeated_same_or_higher_price:
            parts.append(f"active-cache repeat same/higher hidden: **{self.repeated_same_or_higher_price}**")
        if self.lower_price_repeats:
            parts.append(f"lower-price repeats allowed: **{self.lower_price_repeats}**")
        if self.unknown_price_repeats:
            parts.append(f"active-cache repeat unknown-price hidden: **{self.unknown_price_repeats}**")
        return " • ".join(parts)


async def select_fresh_deal_cards(
    db,
    *,
    guild_id: int | None,
    cards: list[Any],
    fallback_retailer: str = "walmart",
    limit: int = 5,
    allow_lower_price_repeat: bool = True,
    min_alert_score: int = 90,
    hide_active_cache_repeats: bool = True,
    min_public_discount: int = 50,
    source_label: str = "",
) -> FreshDealSelection:
    """Return cards that the public post guard is likely to actually post.

    Each processed card receives ``autoscan_preflight_reason``. Reports can then
    identify the exact quality, duplicate, reservation, or active-cache gate
    instead of collapsing every failure into one vague preflight message.
    """
    if not cards:
        return FreshDealSelection(fresh=[])
    if guild_id is None:
        selected = public_alertable_cards(cards, min_alert_score=min_alert_score)[:limit]
        selected_ids = {id(card) for card in selected}
        for card in cards:
            if id(card) in selected_ids:
                _set_preflight_reason(card, "passed public quality preflight")
            else:
                _set_preflight_reason(
                    card,
                    public_quality_block_reason(
                        card,
                        source_label=source_label,
                        min_discount=min_public_discount,
                    ),
                )
        return FreshDealSelection(fresh=selected)

    await ensure_public_post_tables(db)
    conn = db.require_conn()
    fresh: list[Any] = []
    repeated_same_or_higher_price = 0
    lower_price_repeats = 0
    unknown_price_repeats = 0
    recent_public_duplicates = 0
    exact_public_post_duplicates = 0
    stale_reserved_recovered = 0
    not_alertable = 0

    for card in cards:
        _set_preflight_reason(card, "preflight not evaluated")
        retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
        if not prepare_public_deal_candidate(card, source_label=source_label, min_discount=min_public_discount):
            not_alertable += 1
            _set_preflight_reason(
                card,
                public_quality_block_reason(
                    card,
                    source_label=source_label,
                    min_discount=min_public_discount,
                ),
            )
            continue

        current_price = float_or_none(getattr(card, "current_price", None))
        product_key = card_product_key(card, retailer=retailer)
        recent_alert = await safe_find_recent_alert(
            db,
            guild_id=guild_id,
            retailer=retailer,
            product_key=product_key,
            current_price=current_price,
        )
        if recent_alert and should_suppress_recent_alert(recent_alert, current_price):
            recent_public_duplicates += 1
            _set_preflight_reason(
                card,
                "recent public alert already exists at the same or a better price",
            )
            continue

        deal_key = card_deal_key(card, retailer=retailer)
        cursor = await conn.execute(
            "SELECT status, first_seen_at FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ? LIMIT 1",
            (guild_id, deal_key),
        )
        row = await cursor.fetchone()
        if row and public_post_row_should_block(row):
            exact_public_post_duplicates += 1
            status = str(row_value(row, "status") or "recorded")
            if status == "reserved":
                reason = "an active public-post reservation already owns this exact deal"
            else:
                reason = "this exact deal fingerprint was already posted"
            _set_preflight_reason(card, reason)
            continue
        if row:
            stale_reserved_recovered += 1

        if hide_active_cache_repeats:
            key = active_cache_key(
                retailer=retailer,
                url=getattr(card, "url", "") or "",
                selected_offer_id=getattr(card, "selected_offer_id", None),
                sku=getattr(card, "sku", None),
                upc=getattr(card, "upc", None),
            )
            cursor = await conn.execute(
                "SELECT current_price FROM guild_active_deal_cache WHERE guild_id = ? AND active_key = ? AND status = 'active'",
                (guild_id, key),
            )
            row = await cursor.fetchone()
            if row:
                old_price = float_or_none(row_value(row, "current_price"))
                new_price = current_price
                if allow_lower_price_repeat and old_price is not None and new_price is not None and new_price < old_price:
                    lower_price_repeats += 1
                    fresh.append(card)
                    _set_preflight_reason(
                        card,
                        f"lower-price repeat allowed: ${new_price:.2f} is below cached ${old_price:.2f}",
                    )
                elif old_price is None or new_price is None:
                    unknown_price_repeats += 1
                    _set_preflight_reason(
                        card,
                        "active-cache repeat has an unknown old or current price",
                    )
                else:
                    repeated_same_or_higher_price += 1
                    _set_preflight_reason(
                        card,
                        f"active-cache repeat is same/higher: ${new_price:.2f} vs cached ${old_price:.2f}",
                    )
            else:
                fresh.append(card)
                _set_preflight_reason(card, "passed quality, duplicate, reservation, and freshness preflight")
        else:
            fresh.append(card)
            _set_preflight_reason(card, "passed quality and public duplicate preflight")

        if len(fresh) >= limit:
            break
    return FreshDealSelection(
        fresh=fresh,
        repeated_same_or_higher_price=repeated_same_or_higher_price,
        lower_price_repeats=lower_price_repeats,
        unknown_price_repeats=unknown_price_repeats,
        recent_public_duplicates=recent_public_duplicates,
        exact_public_post_duplicates=exact_public_post_duplicates,
        stale_reserved_recovered=stale_reserved_recovered,
        not_alertable=not_alertable,
    )


def public_alertable_cards(cards: list[Any], *, min_alert_score: int = 90) -> list[Any]:
    return [card for card in cards if card_is_public_alertable(card, min_alert_score=min_alert_score)]


def card_is_public_alertable(card: Any, *, min_alert_score: int = 90) -> bool:
    should_alert = getattr(card, "should_alert", None)
    if should_alert is not None:
        return bool(should_alert)
    return is_public_deal_candidate(card, min_discount=50) or int(getattr(card, "score", 0) or 0) >= min_alert_score


def public_post_row_should_block(row: Any) -> bool:
    status = row_value(row, "status")
    if status == "posted":
        return True
    if status != "reserved":
        return False
    first_seen = parse_iso_datetime(row_value(row, "first_seen_at"))
    if first_seen is None:
        return False
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - first_seen < timedelta(minutes=RESERVATION_STALE_MINUTES)


def row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key)
        return getattr(row, key, None)


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _set_preflight_reason(card: Any, reason: str) -> None:
    try:
        setattr(card, PREFLIGHT_REASON_ATTR, " ".join(str(reason or "").split())[:300])
    except Exception:
        pass
