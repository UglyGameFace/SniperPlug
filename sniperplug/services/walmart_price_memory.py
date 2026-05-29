from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.public_posting import normalize_retailer_key


@dataclass(frozen=True)
class PriceMemoryDecision:
    card: Any
    status: str
    reason: str
    previous_price: float | None = None
    current_price: float | None = None
    lowest_seen_price: float | None = None
    previous_coupon: float | None = None
    current_coupon: float | None = None
    previous_cash: float | None = None
    current_cash: float | None = None

    @property
    def should_show(self) -> bool:
        return self.status in {"new", "lower_price", "new_low", "better_value", "offer_changed", "restocked"}


@dataclass(frozen=True)
class PriceMemorySelection:
    shown: list[Any]
    decisions: list[PriceMemoryDecision]

    def summary_line(self) -> str:
        counts: dict[str, int] = {}
        for decision in self.decisions:
            counts[decision.status] = counts.get(decision.status, 0) + 1
        if not counts:
            return "price memory: no cards checked"
        order = ("new", "new_low", "lower_price", "better_value", "offer_changed", "restocked", "same_or_higher", "unknown_price")
        return " • ".join(f"{label}: **{counts[label]}**" for label in order if counts.get(label))


async def select_price_intelligent_cards(
    db,
    *,
    guild_id: int | None,
    cards: list[Any],
    fallback_retailer: str = "walmart",
    limit: int | None = None,
) -> PriceMemorySelection:
    if guild_id is None or not cards:
        return PriceMemorySelection(shown=cards[: limit or len(cards)], decisions=[PriceMemoryDecision(card=c, status="new", reason="no guild memory available") for c in cards])

    await ensure_price_memory_table(db)
    conn = db.require_conn()
    shown: list[Any] = []
    decisions: list[PriceMemoryDecision] = []
    now = datetime.now(timezone.utc).isoformat()

    for card in cards:
        retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
        identity = memory_identity(card, retailer=retailer)
        current_price = float_or_none(getattr(card, "current_price", None))
        current_coupon = float_or_none(getattr(card, "coupon_savings", None) or card_attr(card, "couponSavings"))
        current_cash = float_or_none(card_attr(card, "walmartCashSavings"))
        selected_offer_id = getattr(card, "selected_offer_id", None)
        sku = getattr(card, "sku", None)
        upc = getattr(card, "upc", None)

        cursor = await conn.execute(
            "SELECT * FROM walmart_price_memory WHERE guild_id = ? AND identity_key = ?",
            (guild_id, identity),
        )
        row = await cursor.fetchone()
        decision = decide(card, row=row, current_price=current_price, current_coupon=current_coupon, current_cash=current_cash, selected_offer_id=selected_offer_id)
        decisions.append(decision)

        lowest_seen = current_price
        if row and row["lowest_seen_price"] is not None and current_price is not None:
            lowest_seen = min(float(row["lowest_seen_price"]), current_price)
        elif row and row["lowest_seen_price"] is not None:
            lowest_seen = float(row["lowest_seen_price"])

        await conn.execute(
            """
            INSERT INTO walmart_price_memory (
                guild_id, identity_key, retailer, title, url, sku, upc, selected_offer_id,
                current_price, previous_price, lowest_seen_price, discount, coupon_savings,
                walmart_cash, seller_name, condition, fulfillment_type, stock_status,
                first_seen_at, last_seen_at, last_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, identity_key) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                sku = excluded.sku,
                upc = excluded.upc,
                selected_offer_id = excluded.selected_offer_id,
                previous_price = walmart_price_memory.current_price,
                current_price = excluded.current_price,
                lowest_seen_price = CASE
                    WHEN walmart_price_memory.lowest_seen_price IS NULL THEN excluded.lowest_seen_price
                    WHEN excluded.current_price IS NULL THEN walmart_price_memory.lowest_seen_price
                    WHEN excluded.current_price < walmart_price_memory.lowest_seen_price THEN excluded.current_price
                    ELSE walmart_price_memory.lowest_seen_price
                END,
                discount = excluded.discount,
                coupon_savings = excluded.coupon_savings,
                walmart_cash = excluded.walmart_cash,
                seller_name = excluded.seller_name,
                condition = excluded.condition,
                fulfillment_type = excluded.fulfillment_type,
                stock_status = excluded.stock_status,
                last_seen_at = excluded.last_seen_at,
                last_status = excluded.last_status
            """,
            (
                guild_id,
                identity,
                retailer,
                getattr(card, "label", None) or "deal",
                getattr(card, "url", None),
                sku,
                upc,
                selected_offer_id,
                current_price,
                lowest_seen,
                float_or_none(getattr(card, "discount", None)),
                current_coupon,
                current_cash,
                getattr(card, "seller_name", None),
                getattr(card, "condition", None),
                getattr(card, "fulfillment_type", None),
                getattr(card, "stock_status", None),
                now,
                now,
                decision.status,
            ),
        )

        if decision.should_show:
            shown.append(card)
            attach_memory_badge(card, decision)
            if limit is not None and len(shown) >= limit:
                # keep recording decisions for already visited cards only; caller can run again later
                pass

    await conn.commit()
    if limit is not None:
        shown = shown[:limit]
    return PriceMemorySelection(shown=shown, decisions=decisions)


async def remembered_walmart_search_seeds(db, *, guild_id: int | None, limit: int = 30) -> tuple[str, ...]:
    """Return direct recheck seeds from products SniperPlug has already seen.

    Route scans miss products when Walmart stops returning them for a keyword.
    Remembered seeds let every hunt recheck known SKUs/UPCs/titles directly so
    previously seen good products can resurface on price drops, new lows, coupon
    changes, offer changes, or restocks.
    """
    if db is None or guild_id is None:
        return ()
    await ensure_price_memory_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        """
        SELECT sku, upc, title, selected_offer_id, last_status, lowest_seen_price, current_price
        FROM walmart_price_memory
        WHERE guild_id = ? AND retailer = 'walmart'
        ORDER BY
            CASE last_status
                WHEN 'new_low' THEN 0
                WHEN 'lower_price' THEN 1
                WHEN 'better_value' THEN 2
                WHEN 'offer_changed' THEN 3
                WHEN 'new' THEN 4
                ELSE 5
            END,
            last_seen_at DESC
        LIMIT ?
        """,
        (guild_id, max(1, limit * 2)),
    )
    rows = await cursor.fetchall()
    seeds: list[str] = []
    for row in rows:
        for value in (row["sku"], row["upc"], row["selected_offer_id"], compact_title_seed(row["title"])):
            text = str(value or "").strip()
            if text and text.lower() not in {item.lower() for item in seeds}:
                seeds.append(text)
            if len(seeds) >= limit:
                return tuple(seeds)
    return tuple(seeds)


def decide(card: Any, *, row, current_price: float | None, current_coupon: float | None, current_cash: float | None, selected_offer_id: str | None) -> PriceMemoryDecision:
    if row is None:
        return PriceMemoryDecision(card=card, status="new", reason="new API-verified offer", current_price=current_price)
    previous_price = float_or_none(row["current_price"])
    lowest_seen = float_or_none(row["lowest_seen_price"])
    previous_coupon = float_or_none(row["coupon_savings"])
    previous_cash = float_or_none(row["walmart_cash"])
    previous_offer_id = row["selected_offer_id"]

    if selected_offer_id and previous_offer_id and selected_offer_id != previous_offer_id:
        return PriceMemoryDecision(card=card, status="offer_changed", reason="selected offer ID changed", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen)
    if current_price is None:
        return PriceMemoryDecision(card=card, status="unknown_price", reason="current price missing from card", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen)
    if previous_price is None:
        return PriceMemoryDecision(card=card, status="new", reason="first remembered price", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen)
    if lowest_seen is not None and current_price < lowest_seen:
        return PriceMemoryDecision(card=card, status="new_low", reason="new lowest seen price", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen)
    if current_price < previous_price:
        return PriceMemoryDecision(card=card, status="lower_price", reason="lower than last seen price", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen)
    if (current_coupon or 0) > (previous_coupon or 0) or (current_cash or 0) > (previous_cash or 0):
        return PriceMemoryDecision(card=card, status="better_value", reason="coupon or Walmart Cash improved", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen, previous_coupon=previous_coupon, current_coupon=current_coupon, previous_cash=previous_cash, current_cash=current_cash)
    return PriceMemoryDecision(card=card, status="same_or_higher", reason="same or higher than remembered price", previous_price=previous_price, current_price=current_price, lowest_seen_price=lowest_seen)


def attach_memory_badge(card: Any, decision: PriceMemoryDecision) -> None:
    embed = getattr(card, "embed", None)
    if embed is None:
        return
    lines = [f"Status: **{decision.status.replace('_', ' ').title()}**", f"Reason: {decision.reason}"]
    if decision.previous_price is not None and decision.current_price is not None:
        lines.append(f"Previous: ${decision.previous_price:,.2f} → Current: ${decision.current_price:,.2f}")
    if decision.lowest_seen_price is not None:
        lines.append(f"Previous lowest seen: ${decision.lowest_seen_price:,.2f}")
    embed.add_field(name="🧠 Price memory", value="\n".join(lines), inline=False)


def memory_identity(card: Any, *, retailer: str) -> str:
    identity = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or canonical_url_key(getattr(card, "url", ""))
    return f"{normalize_retailer_key(retailer)}:{identity}"


def card_attr(card: Any, key: str):
    attrs = getattr(card, "variant_attributes", None) or getattr(card, "api_attrs", None) or {}
    if isinstance(attrs, dict):
        return attrs.get(key)
    return None


def canonical_url_key(url: str) -> str:
    text = (url or "").strip()
    return text.split("?", 1)[0].rstrip("/") or "unknown"


async def ensure_price_memory_table(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS walmart_price_memory (
            guild_id INTEGER NOT NULL,
            identity_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            sku TEXT,
            upc TEXT,
            selected_offer_id TEXT,
            current_price REAL,
            previous_price REAL,
            lowest_seen_price REAL,
            discount REAL,
            coupon_savings REAL,
            walmart_cash REAL,
            seller_name TEXT,
            condition TEXT,
            fulfillment_type TEXT,
            stock_status TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_status TEXT NOT NULL,
            PRIMARY KEY (guild_id, identity_key)
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_walmart_price_memory_guild_status ON walmart_price_memory (guild_id, last_status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_walmart_price_memory_guild_price ON walmart_price_memory (guild_id, current_price)")
    await conn.commit()


def compact_title_seed(title: str | None) -> str:
    words = [word.strip(" ,-/|()[]{}") for word in str(title or "").split()]
    useful = [word for word in words if len(word) >= 3 and word.lower() not in {"the", "and", "for", "with", "walmart"}]
    return " ".join(useful[:6])[:80]


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value).replace("$", "").replace(",", "").strip())
        except (TypeError, ValueError):
            return None
