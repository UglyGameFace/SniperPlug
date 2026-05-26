from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.public_posting import normalize_retailer_key


@dataclass(frozen=True)
class FreshnessDecision:
    status: str
    should_show: bool
    previous_price: float | None = None
    best_price: float | None = None
    price_delta: float | None = None
    reason: str = ""

    @property
    def is_improved(self) -> bool:
        return self.status in {"new", "price_drop", "new_best_price", "better_discount", "first_trusted_proof"}


def deal_identity(*, retailer: str, url: str = "", selected_offer_id: str | None = None, sku: str | None = None, upc: str | None = None) -> str:
    identity = selected_offer_id or sku or upc or canonical_url_key(url)
    return ":".join((normalize_retailer_key(retailer), identity))


def canonical_url_key(url: str) -> str:
    text = (url or "").strip()
    return text.split("?", 1)[0].rstrip("/") or "unknown"


def current_price_of(card: Any) -> float | None:
    value = getattr(card, "current_price", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def discount_of(card: Any) -> float | None:
    value = getattr(card, "discount", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def ensure_freshness_tables(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_deal_freshness (
            guild_id INTEGER NOT NULL,
            identity_key TEXT NOT NULL,
            retailer TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            current_price REAL,
            previous_price REAL,
            best_price REAL,
            discount REAL,
            best_discount REAL,
            score INTEGER,
            times_seen INTEGER NOT NULL DEFAULT 0,
            last_status TEXT NOT NULL DEFAULT 'new',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_shown_at TEXT,
            PRIMARY KEY (guild_id, identity_key)
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_freshness_guild_status ON guild_deal_freshness (guild_id, retailer, last_status)")
    await conn.commit()


async def evaluate_card_freshness(db, *, guild_id: int, card: Any, fallback_retailer: str = "walmart", force_show_repeats: bool = False) -> FreshnessDecision:
    await ensure_freshness_tables(db)
    conn = db.require_conn()
    retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
    identity = deal_identity(
        retailer=retailer,
        url=getattr(card, "url", ""),
        selected_offer_id=getattr(card, "selected_offer_id", None),
        sku=getattr(card, "sku", None),
        upc=getattr(card, "upc", None),
    )
    now = datetime.now(timezone.utc).isoformat()
    current_price = current_price_of(card)
    discount = discount_of(card)
    score = int(getattr(card, "score", 0) or 0)
    label = str(getattr(card, "label", None) or "deal")
    url = str(getattr(card, "url", "") or "")

    cursor = await conn.execute("SELECT * FROM guild_deal_freshness WHERE guild_id = ? AND identity_key = ?", (guild_id, identity))
    row = await cursor.fetchone()

    if not row:
        await conn.execute(
            """
            INSERT INTO guild_deal_freshness (
                guild_id, identity_key, retailer, title, url, current_price, previous_price, best_price,
                discount, best_discount, score, times_seen, last_status, first_seen_at, last_seen_at, last_shown_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 1, 'new', ?, ?, ?)
            """,
            (guild_id, identity, retailer, label, url, current_price, current_price, discount, discount, score, now, now, now),
        )
        await conn.commit()
        annotate_card(card, "new", "New candidate SniperPlug has not seen before.")
        return FreshnessDecision(status="new", should_show=True, previous_price=None, best_price=current_price, reason="New candidate SniperPlug has not seen before.")

    previous_price = row["current_price"]
    best_price = row["best_price"]
    previous_best_discount = row["best_discount"]
    status = "repeat"
    reason = "Same product/offer seen before with no better price proof."
    should_show = bool(force_show_repeats)

    if current_price is not None and previous_price is not None and current_price < float(previous_price):
        status = "price_drop"
        should_show = True
        reason = f"Price dropped from ${float(previous_price):,.2f} to ${current_price:,.2f}."
    if current_price is not None and (best_price is None or current_price < float(best_price)):
        status = "new_best_price"
        should_show = True
        reason = f"New best-seen price: ${current_price:,.2f}."
    if discount is not None and previous_best_discount is not None and discount > float(previous_best_discount):
        status = "better_discount"
        should_show = True
        reason = f"Discount proof improved from {float(previous_best_discount):.0f}% to {discount:.0f}%."

    new_best_price = current_price
    if best_price is not None and current_price is not None:
        new_best_price = min(float(best_price), current_price)
    elif best_price is not None:
        new_best_price = float(best_price)

    new_best_discount = discount
    if previous_best_discount is not None and discount is not None:
        new_best_discount = max(float(previous_best_discount), discount)
    elif previous_best_discount is not None:
        new_best_discount = float(previous_best_discount)

    last_shown = now if should_show else row["last_shown_at"]
    await conn.execute(
        """
        UPDATE guild_deal_freshness
        SET title = ?, url = ?, previous_price = current_price, current_price = ?, best_price = ?,
            discount = ?, best_discount = ?, score = ?, times_seen = times_seen + 1,
            last_status = ?, last_seen_at = ?, last_shown_at = ?
        WHERE guild_id = ? AND identity_key = ?
        """,
        (label, url, current_price, new_best_price, discount, new_best_discount, score, status, now, last_shown, guild_id, identity),
    )
    await conn.commit()

    price_delta = None
    if current_price is not None and previous_price is not None:
        price_delta = current_price - float(previous_price)
    annotate_card(card, status, reason)
    return FreshnessDecision(status=status, should_show=should_show, previous_price=previous_price, best_price=new_best_price, price_delta=price_delta, reason=reason)


async def filter_fresh_cards(db, *, guild_id: int, cards: list[Any], fallback_retailer: str = "walmart", force_show_repeats: bool = False, max_cards: int = 5) -> tuple[list[Any], list[FreshnessDecision]]:
    decisions: list[FreshnessDecision] = []
    fresh_cards: list[Any] = []
    for card in cards:
        decision = await evaluate_card_freshness(db, guild_id=guild_id, card=card, fallback_retailer=fallback_retailer, force_show_repeats=force_show_repeats)
        decisions.append(decision)
        if decision.should_show:
            fresh_cards.append(card)
        if len(fresh_cards) >= max_cards:
            break
    if not fresh_cards and cards and force_show_repeats:
        fresh_cards = cards[:max_cards]
    return fresh_cards[:max_cards], decisions


def annotate_card(card: Any, status: str, reason: str) -> None:
    setattr(card, "freshness_status", status)
    setattr(card, "freshness_reason", reason)
    embed = getattr(card, "embed", None)
    if embed is not None and hasattr(embed, "add_field"):
        try:
            if status != "repeat":
                embed.add_field(name="🆕 Freshness", value=reason, inline=False)
        except Exception:
            return
