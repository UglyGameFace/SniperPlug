from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sniperplug.services.public_deal_posts import active_cache_key, ensure_public_post_tables
from sniperplug.services.public_posting import normalize_retailer_key


@dataclass(frozen=True)
class FreshDealSelection:
    fresh: list[Any]
    repeated_same_or_higher_price: int = 0
    lower_price_repeats: int = 0
    unknown_price_repeats: int = 0

    @property
    def skipped_repeats(self) -> int:
        return self.repeated_same_or_higher_price + self.unknown_price_repeats

    def summary_line(self) -> str:
        parts = [f"fresh/new: **{len(self.fresh)}**"]
        if self.repeated_same_or_higher_price:
            parts.append(f"repeat same/higher price hidden: **{self.repeated_same_or_higher_price}**")
        if self.lower_price_repeats:
            parts.append(f"lower-price repeats allowed: **{self.lower_price_repeats}**")
        if self.unknown_price_repeats:
            parts.append(f"repeat unknown-price hidden: **{self.unknown_price_repeats}**")
        return " • ".join(parts)


async def select_fresh_deal_cards(
    db,
    *,
    guild_id: int | None,
    cards: list[Any],
    fallback_retailer: str = "walmart",
    limit: int = 5,
    allow_lower_price_repeat: bool = True,
) -> FreshDealSelection:
    """Prefer unseen cards so SniperPlug stops showing the same finds forever.

    A repeated product is hidden when its current price is the same or higher than
    the active cache. If the same product appears at a lower price, it is allowed
    through because that is exactly the kind of update worth showing/posting.
    """
    if guild_id is None or not cards:
        return FreshDealSelection(fresh=cards[:limit])

    await ensure_public_post_tables(db)
    conn = db.require_conn()
    fresh: list[Any] = []
    repeated_same_or_higher_price = 0
    lower_price_repeats = 0
    unknown_price_repeats = 0

    for card in cards:
        retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
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
        if not row:
            fresh.append(card)
        else:
            old_price = float_or_none(row["current_price"])
            new_price = float_or_none(getattr(card, "current_price", None))
            if allow_lower_price_repeat and old_price is not None and new_price is not None and new_price < old_price:
                lower_price_repeats += 1
                fresh.append(card)
            elif old_price is None or new_price is None:
                unknown_price_repeats += 1
            else:
                repeated_same_or_higher_price += 1
        if len(fresh) >= limit:
            break

    return FreshDealSelection(
        fresh=fresh,
        repeated_same_or_higher_price=repeated_same_or_higher_price,
        lower_price_repeats=lower_price_repeats,
        unknown_price_repeats=unknown_price_repeats,
    )


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
