from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from sniperplug.services.public_deal_posts import card_product_key, ensure_public_post_tables
from sniperplug.services.public_posting import normalize_retailer_key


ACTIVE_CACHE_STALE_HOURS = 24
ACTIVE_CACHE_QUERY_LIMIT = 500


@dataclass(frozen=True)
class CachedDealRow:
    active_key: str
    retailer: str
    title: str
    url: str
    current_price: float | None
    discount: float | None
    score: int | None
    source_label: str
    status: str
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True)
class ScanFreshness:
    cached_before: int = 0
    new_cards: tuple[Any, ...] = ()
    price_drop_cards: tuple[Any, ...] = ()
    repeat_cards: tuple[Any, ...] = ()

    @property
    def new_count(self) -> int:
        return len(self.new_cards)

    @property
    def price_drop_count(self) -> int:
        return len(self.price_drop_cards)

    @property
    def repeat_count(self) -> int:
        return len(self.repeat_cards)


async def mark_stale_active_deals(db, guild_id: int, *, stale_after_hours: int = ACTIVE_CACHE_STALE_HOURS) -> int:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(stale_after_hours)))
    cursor = await conn.execute(
        "UPDATE guild_active_deal_cache SET status = 'stale' WHERE guild_id = ? AND status = 'active' AND last_seen_at < ?",
        (guild_id, cutoff.isoformat()),
    )
    await conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0)


async def list_cached_active_deals(db, guild_id: int, *, retailer: str = "walmart", query: str | None = None, limit: int = 8) -> list[CachedDealRow]:
    await ensure_public_post_tables(db)
    await mark_stale_active_deals(db, guild_id)
    conn = db.require_conn()
    safe_limit = max(1, min(int(limit), ACTIVE_CACHE_QUERY_LIMIT))
    retailer_key = normalize_retailer_key(retailer) or retailer
    terms = significant_terms(query or "")[:4]
    params: list[Any] = [guild_id, retailer_key]
    where = "guild_id = ? AND retailer = ? AND status = 'active'"
    for term in terms:
        where += " AND LOWER(title) LIKE ?"
        params.append(f"%{term.lower()}%")
    params.append(safe_limit)
    cursor = await conn.execute(
        f"""
        SELECT active_key, retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at
        FROM guild_active_deal_cache
        WHERE {where}
        ORDER BY last_seen_at DESC, score DESC, discount DESC
        LIMIT ?
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [row_from_mapping(dict(row)) for row in rows]


async def active_cache_snapshot(db, guild_id: int, *, retailer: str = "walmart", query: str | None = None, limit: int = 100) -> dict[str, CachedDealRow]:
    rows = await list_cached_active_deals(db, guild_id, retailer=retailer, query=query, limit=limit)
    return {row.active_key: row for row in rows if row.active_key}


def classify_scan_freshness(cards: list[Any], cached_before: dict[str, CachedDealRow], *, fallback_retailer: str = "walmart") -> ScanFreshness:
    new_cards: list[Any] = []
    price_drop_cards: list[Any] = []
    repeat_cards: list[Any] = []
    for card in cards:
        key = safe_card_key(card, fallback_retailer=fallback_retailer)
        previous = cached_before.get(key)
        if previous is None:
            new_cards.append(card)
            continue
        current = float_or_none(getattr(card, "current_price", None))
        previous_price = previous.current_price
        if current is not None and previous_price is not None and current < previous_price:
            price_drop_cards.append(card)
        else:
            repeat_cards.append(card)
    return ScanFreshness(
        cached_before=len(cached_before),
        new_cards=tuple(new_cards),
        price_drop_cards=tuple(price_drop_cards),
        repeat_cards=tuple(repeat_cards),
    )


def build_cached_active_embed(query: str, rows: list[CachedDealRow]) -> discord.Embed:
    embed = discord.Embed(
        title="⚡ Cached Active Deals",
        description=(
            f"Search: **{query}**\n"
            "These were already seen recently, so SniperPlug can show them instantly while fresh scans look for changes. Recheck before buying."
        ),
        color=discord.Color.green() if rows else discord.Color.dark_gold(),
    )
    if not rows:
        embed.add_field(name="No matching cached active deals", value="Run a fresh scan first, then this cache gets smarter.", inline=False)
        return embed
    for row in rows[:8]:
        discount = f"{row.discount:.0f}%" if row.discount is not None else "n/a"
        score = row.score if row.score is not None else "n/a"
        embed.add_field(
            name=f"{row.retailer} • {trim(row.title, 72)}",
            value=(
                f"Price: **{money(row.current_price)}** • Discount: **{discount}** • Score: `{score}`\n"
                f"Source: `{row.source_label}` • Last seen: `{row.last_seen_at}`\n"
                f"{row.url}"
            ),
            inline=False,
        )
    embed.set_footer(text="Cached Active does not spend a Walmart search. Fresh Scan bypasses this cache.")
    return embed


def build_new_since_scan_embed(query: str, freshness: ScanFreshness) -> discord.Embed:
    embed = discord.Embed(
        title="🆕 New / Changed Since Scan Start",
        description=(
            f"Search: **{query}**\n"
            f"Cached before scan: **{freshness.cached_before}**\n"
            f"New: **{freshness.new_count}** • Price drops: **{freshness.price_drop_count}** • Repeat same/higher: **{freshness.repeat_count}**"
        ),
        color=discord.Color.red() if freshness.new_count or freshness.price_drop_count else discord.Color.orange(),
    )
    cards = list(freshness.price_drop_cards[:5]) + list(freshness.new_cards[:5])
    if not cards:
        embed.add_field(name="No brand-new or lower-price cards", value="Fresh scan found only repeat same/higher-price cards for this result set.", inline=False)
        return embed
    for card in cards[:8]:
        marker = "📉 Price drop" if card in freshness.price_drop_cards else "🆕 New"
        embed.add_field(
            name=f"{marker} • {trim(str(getattr(card, 'label', 'deal')), 72)}",
            value=(
                f"Price: **{money(getattr(card, 'current_price', None))}** • Discount: **{float(getattr(card, 'discount', 0) or 0):.0f}%** • Score: `{getattr(card, 'score', 'n/a')}`\n"
                f"{getattr(card, 'url', '')}"
            ),
            inline=False,
        )
    embed.set_footer(text="New/changed uses the cache snapshot from right before this scan started.")
    return embed


def row_from_mapping(row: dict[str, Any]) -> CachedDealRow:
    return CachedDealRow(
        active_key=str(row.get("active_key") or ""),
        retailer=str(row.get("retailer") or "retailer"),
        title=str(row.get("title") or "deal"),
        url=str(row.get("url") or ""),
        current_price=float_or_none(row.get("current_price")),
        discount=float_or_none(row.get("discount")),
        score=int_or_none(row.get("score")),
        source_label=str(row.get("source_label") or "unknown"),
        status=str(row.get("status") or "active"),
        first_seen_at=str(row.get("first_seen_at") or "unknown"),
        last_seen_at=str(row.get("last_seen_at") or "unknown"),
    )


def safe_card_key(card: Any, *, fallback_retailer: str = "walmart") -> str:
    retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer) or fallback_retailer
    try:
        return card_product_key(card, retailer=retailer)
    except Exception:
        return ":".join((retailer, str(getattr(card, "sku", None) or getattr(card, "upc", None) or getattr(card, "url", "") or getattr(card, "label", "unknown"))))


def significant_terms(query: str) -> list[str]:
    stop = {"the", "and", "for", "with", "from", "deal", "deals", "sale", "cheap", "best", "walmart"}
    terms = []
    for raw in query.replace("-", " ").split():
        term = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(term) < 3 or term in stop:
            continue
        terms.append(term)
    return terms


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def money(value: Any) -> str:
    parsed = float_or_none(value)
    return "N/A" if parsed is None else f"${parsed:,.2f}"


def trim(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
