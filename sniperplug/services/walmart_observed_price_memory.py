from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.public_deal_quality import LANE_PRICE_MEMORY_DROP
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.safe_links import product_link_choices
from sniperplug.services.walmart_price_memory import ensure_price_memory_table


MIN_MEMORY_DROP_DOLLARS = 5.00


@dataclass(frozen=True)
class ObservedPriceMemoryDecision:
    candidate: SourceCandidate
    identity_key: str
    status: str
    previous_price: float | None
    current_price: float | None
    lowest_seen_price: float | None
    drop_percent: float = 0.0
    drop_dollars: float = 0.0
    reason: str = ""

    @property
    def should_public_post(self) -> bool:
        return self.status in {"lower_price", "new_low"} and self.drop_percent > 0 and self.drop_dollars > 0


@dataclass(frozen=True)
class ObservedPriceMemorySelection:
    cards: list[DealCard]
    decisions: list[ObservedPriceMemoryDecision]

    def summary_line(self) -> str:
        counts: dict[str, int] = {}
        for decision in self.decisions:
            counts[decision.status] = counts.get(decision.status, 0) + 1
        if not counts:
            return "observed price memory: no products checked"
        order = ("new", "new_low", "lower_price", "same_or_higher", "missing_identity", "missing_price", "not_buyable")
        parts = [f"{label}: **{counts[label]}**" for label in order if counts.get(label)]
        parts.append(f"public price-drop cards: **{len(self.cards)}**")
        return "observed price memory: " + " • ".join(parts)


async def select_observed_price_drop_cards(db: Any, *, guild_id: int | None, candidates: list[SourceCandidate], min_discount: int = 50, limit: int = 5) -> ObservedPriceMemorySelection:
    """Record exact Walmart candidates and return public-safe observed price drops."""

    if db is None or guild_id is None or not candidates:
        return ObservedPriceMemorySelection(cards=[], decisions=[])

    await ensure_price_memory_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    decisions: list[ObservedPriceMemoryDecision] = []
    cards: list[DealCard] = []

    for candidate in candidates:
        retailer = normalize_retailer_key(candidate.retailer) or "walmart"
        if retailer != "walmart":
            continue
        identity = candidate_identity(candidate)
        current_price = float_or_none(candidate.current_price)
        buyable = is_candidate_buyable(candidate)
        if not identity:
            decisions.append(ObservedPriceMemoryDecision(candidate, "", "missing_identity", None, current_price, None, reason="no stable Walmart item identity"))
            continue
        identity_key = f"walmart:{identity}"
        cursor = await conn.execute("SELECT * FROM walmart_price_memory WHERE guild_id = ? AND identity_key = ?", (guild_id, identity_key))
        row = await cursor.fetchone()
        lowest_seen = float_or_none(row["lowest_seen_price"]) if row else None
        decision = decide_candidate(candidate, identity_key=identity_key, row=row, current_price=current_price, buyable=buyable, min_discount=min_discount)
        decisions.append(decision)
        await upsert_candidate_memory(conn, guild_id=guild_id, identity_key=identity_key, candidate=candidate, current_price=current_price, lowest_seen=lowest_seen, now=now, status=decision.status)
        if decision.should_public_post and len(cards) < max(1, int(limit)):
            cards.append(build_observed_price_drop_card(candidate, decision, min_discount=min_discount))

    await conn.commit()
    cards.sort(key=lambda card: float(getattr(card, "api_discount_percent", 0) or 0), reverse=True)
    return ObservedPriceMemorySelection(cards=cards[:limit], decisions=decisions)


def decide_candidate(candidate: SourceCandidate, *, identity_key: str, row: Any, current_price: float | None, buyable: bool, min_discount: int) -> ObservedPriceMemoryDecision:
    if current_price is None or current_price <= 0:
        return ObservedPriceMemoryDecision(candidate, identity_key, "missing_price", None, current_price, None, reason="current API price missing")
    previous_price = float_or_none(row["current_price"]) if row else None
    lowest_seen = float_or_none(row["lowest_seen_price"]) if row else None
    if row is None:
        return ObservedPriceMemoryDecision(candidate, identity_key, "new", None, current_price, current_price, reason="first time this exact Walmart item was observed")
    if not buyable:
        return ObservedPriceMemoryDecision(candidate, identity_key, "not_buyable", previous_price, current_price, lowest_seen, reason="current row is not buyable enough for public posting")
    reference = previous_price
    status = "lower_price"
    if lowest_seen is not None and current_price < lowest_seen:
        reference = lowest_seen
        status = "new_low"
    if reference is None or reference <= current_price:
        return ObservedPriceMemoryDecision(candidate, identity_key, "same_or_higher", previous_price, current_price, lowest_seen, reason="current API price is not below remembered price")
    drop_dollars = round(reference - current_price, 2)
    drop_percent = round((reference - current_price) / reference * 100, 2)
    if drop_percent < max(1, int(min_discount)) or drop_dollars < MIN_MEMORY_DROP_DOLLARS:
        return ObservedPriceMemoryDecision(candidate, identity_key, "same_or_higher", previous_price, current_price, lowest_seen, drop_percent=drop_percent, drop_dollars=drop_dollars, reason="observed drop below public threshold")
    return ObservedPriceMemoryDecision(candidate, identity_key, status, previous_price, current_price, lowest_seen, drop_percent=drop_percent, drop_dollars=drop_dollars, reason="same exact Walmart item is lower than SniperPlug previously observed")


def build_observed_price_drop_card(candidate: SourceCandidate, decision: ObservedPriceMemoryDecision, *, min_discount: int) -> DealCard:
    reference = decision.lowest_seen_price if decision.status == "new_low" and decision.lowest_seen_price else decision.previous_price
    embed = discord.Embed(
        title=f"📉 Walmart price drop • {_short(candidate.title, 82)}",
        url=candidate.direct_product_url or candidate.product_url,
        description="Same exact Walmart item is now lower than a prior API-observed price. No MSRP/list text, marketplace comps, Walmart Cash, or query words are used as proof.",
        color=discord.Color.green(),
    )
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)
    embed.add_field(name="✅ Observed price-drop proof", value=(f"Previous observed API price: **{money(reference)}**\nCurrent API price: **{money(decision.current_price)}**\nObserved drop: **{decision.drop_percent:.0f}%** / **{money(decision.drop_dollars)}**\nServer threshold: **{int(min_discount)}%+**\nIdentity: `{decision.identity_key}`"), inline=False)
    choices = product_link_choices(retailer=candidate.retailer, product_url=candidate.product_url, title=candidate.title, product_id=candidate.product_id, sku=candidate.sku, upc=candidate.upc)
    card = DealCard(embed=embed, url=candidate.product_url, label=candidate.title, score=100, discount=decision.drop_percent, link_choices=choices, deal_lane=LANE_PRICE_MEMORY_DROP, api_current_price=decision.current_price, api_reference_price=reference, api_discount_percent=decision.drop_percent, api_reference_path="sniperplug.walmart_price_memory.previous_observed_api_price", api_price_path="walmart.api.current_price", seller_name=candidate.seller_name, fulfillment_type=candidate.fulfillment_type, direct_product_url=candidate.direct_product_url or candidate.product_url, variant_attributes={"priceMemoryIdentity": decision.identity_key, "priceMemoryReason": decision.reason, "referencePriceTrusted": "yes"})
    card.retailer = candidate.retailer
    card.current_price = decision.current_price
    card.should_alert = True
    card.sku = candidate.sku
    card.upc = candidate.upc
    card.selected_offer_id = candidate.selected_offer_id
    card.public_post_key = f"price_memory:{decision.identity_key}:{money(decision.current_price)}"
    return card


async def upsert_candidate_memory(conn: Any, *, guild_id: int, identity_key: str, candidate: SourceCandidate, current_price: float | None, lowest_seen: float | None, now: str, status: str) -> None:
    next_lowest = current_price if lowest_seen is None else min(lowest_seen, current_price) if current_price is not None else lowest_seen
    await conn.execute(
        """
        INSERT INTO walmart_price_memory (guild_id, identity_key, retailer, title, url, sku, upc, selected_offer_id, current_price, previous_price, lowest_seen_price, discount, coupon_savings, walmart_cash, seller_name, condition, fulfillment_type, stock_status, first_seen_at, last_seen_at, last_status)
        VALUES (?, ?, 'walmart', ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, identity_key) DO UPDATE SET
            title = excluded.title, url = excluded.url, sku = excluded.sku, upc = excluded.upc, selected_offer_id = excluded.selected_offer_id,
            previous_price = walmart_price_memory.current_price, current_price = excluded.current_price,
            lowest_seen_price = CASE WHEN walmart_price_memory.lowest_seen_price IS NULL THEN excluded.lowest_seen_price WHEN excluded.current_price IS NULL THEN walmart_price_memory.lowest_seen_price WHEN excluded.current_price < walmart_price_memory.lowest_seen_price THEN excluded.current_price ELSE walmart_price_memory.lowest_seen_price END,
            seller_name = excluded.seller_name, condition = excluded.condition, fulfillment_type = excluded.fulfillment_type, stock_status = excluded.stock_status, last_seen_at = excluded.last_seen_at, last_status = excluded.last_status
        """,
        (guild_id, identity_key, candidate.title, candidate.product_url, candidate.sku, candidate.upc, candidate.selected_offer_id, current_price, next_lowest, candidate.seller_name, candidate.condition, candidate.fulfillment_type, candidate.stock_status, now, now, status),
    )


def candidate_identity(candidate: SourceCandidate) -> str:
    for value in (candidate.selected_offer_id, candidate.product_id, candidate.sku, candidate.upc, canonical_url_key(candidate.product_url)):
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "unknown"}:
            return text
    return ""


def is_candidate_buyable(candidate: SourceCandidate) -> bool:
    if candidate.option_mismatch_warning or candidate.is_member_only or candidate.is_checkout_price or candidate.is_business_offer:
        return False
    stock = " ".join(str(candidate.stock_status or "").lower().split())
    if any(term in stock for term in ("out of stock", "unavailable", "not available", "sold out")):
        return False
    if candidate.can_add_to_cart is False:
        return False
    return True


def canonical_url_key(url: str | None) -> str:
    text = str(url or "").strip().split("?", 1)[0].rstrip("/")
    if "/ip/" in text:
        return text.rsplit("/ip/", 1)[-1].strip("/")
    return text


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
