from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sniperplug.services.public_deal_posts import (
    active_cache_key,
    card_deal_key,
    card_product_key,
    ensure_public_post_tables,
    safe_find_recent_alert,
    should_suppress_recent_alert,
)
from sniperplug.services.public_posting import normalize_retailer_key


@dataclass(frozen=True)
class FreshDealSelection:
    fresh: list[Any]
    repeated_same_or_higher_price: int = 0
    lower_price_repeats: int = 0
    unknown_price_repeats: int = 0
    recent_public_duplicates: int = 0
    exact_public_post_duplicates: int = 0
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
) -> FreshDealSelection:
    """Return cards that the public post guard is likely to actually post.

    Public posting is ultimately controlled by `maybe_post_public_deal_cards`.
    This preflight mirrors its most important gates so auto-scan does not say
    "Sent to public guard: 5" when all 5 are already known public duplicates.

    `hide_active_cache_repeats` is optional because the active cache records
    deals SniperPlug has *seen*, not necessarily deals it successfully posted.
    Auto-scan should usually leave this false so correctly configured public
    channels can still receive a deal that was seen while posting was broken.
    """
    if not cards:
        return FreshDealSelection(fresh=[])
    if guild_id is None:
        return FreshDealSelection(fresh=public_alertable_cards(cards, min_alert_score=min_alert_score)[:limit])

    await ensure_public_post_tables(db)
    conn = db.require_conn()
    fresh: list[Any] = []
    repeated_same_or_higher_price = 0
    lower_price_repeats = 0
    unknown_price_repeats = 0
    recent_public_duplicates = 0
    exact_public_post_duplicates = 0
    not_alertable = 0

    for card in cards:
        retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
        if not card_is_public_alertable(card, min_alert_score=min_alert_score):
            not_alertable += 1
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
            continue

        deal_key = card_deal_key(card, retailer=retailer)
        cursor = await conn.execute(
            "SELECT status FROM guild_public_deal_posts WHERE guild_id = ? AND deal_key = ? LIMIT 1",
            (guild_id, deal_key),
        )
        row = await cursor.fetchone()
        if row:
            exact_public_post_duplicates += 1
            continue

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
                old_price = float_or_none(row["current_price"])
                new_price = current_price
                if allow_lower_price_repeat and old_price is not None and new_price is not None and new_price < old_price:
                    lower_price_repeats += 1
                    fresh.append(card)
                elif old_price is None or new_price is None:
                    unknown_price_repeats += 1
                else:
                    repeated_same_or_higher_price += 1
            else:
                fresh.append(card)
        else:
            fresh.append(card)

        if len(fresh) >= limit:
            break

    return FreshDealSelection(
        fresh=fresh,
        repeated_same_or_higher_price=repeated_same_or_higher_price,
        lower_price_repeats=lower_price_repeats,
        unknown_price_repeats=unknown_price_repeats,
        recent_public_duplicates=recent_public_duplicates,
        exact_public_post_duplicates=exact_public_post_duplicates,
        not_alertable=not_alertable,
    )


def public_alertable_cards(cards: list[Any], *, min_alert_score: int = 90) -> list[Any]:
    return [card for card in cards if card_is_public_alertable(card, min_alert_score=min_alert_score)]


def card_is_public_alertable(card: Any, *, min_alert_score: int = 90) -> bool:
    should_alert = getattr(card, "should_alert", None)
    if should_alert is None:
        return int(getattr(card, "score", 0) or 0) >= min_alert_score
    return bool(should_alert)


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
