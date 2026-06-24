from __future__ import annotations

from typing import Any
import re

import discord


SCOUT_GRADE_FIELD = "🚦 SniperPlug Scout Grade"
BUY_CHECK_FIELD = "✅ 20-second buy check"
SCOUT_FOOTER = "Scout Lane: fast lead, not blind-buy proof. Verify before buying."


def _text(card: Any) -> str:
    chunks: list[str] = []
    for attr in ("label", "url", "retailer", "sku", "upc", "selected_offer_id"):
        value = getattr(card, attr, None)
        if value:
            chunks.append(str(value))
    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed):
        chunks.append(str(embed.title or ""))
        chunks.append(str(embed.description or ""))
        for field in getattr(embed, "fields", []) or []:
            chunks.append(str(getattr(field, "name", "") or ""))
            chunks.append(str(getattr(field, "value", "") or ""))
    return "\n".join(chunks)


def _title(card: Any) -> str:
    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and embed.title:
        return str(embed.title)
    return str(getattr(card, "label", "") or "Untitled Walmart scout lead")


def _price(card: Any) -> float:
    try:
        value = getattr(card, "current_price", None)
        return float(value or 0)
    except Exception:
        return 0.0


def _discount(card: Any) -> float:
    try:
        return float(getattr(card, "discount", 0) or 0)
    except Exception:
        return 0.0


def _base_score(card: Any) -> int:
    try:
        return int(float(getattr(card, "score", 0) or 0))
    except Exception:
        return 0


def _money(value: float | None) -> str:
    if value is None:
        return "unknown"
    try:
        return f"${float(value):.2f}"
    except Exception:
        return str(value)


def _dedupe_key(card: Any) -> str:
    for attr in ("selected_offer_id", "sku", "upc", "url", "label"):
        value = getattr(card, attr, None)
        if value:
            return str(value).strip().lower()
    return _title(card).strip().lower()


def scout_reasons(card: Any) -> list[str]:
    text = _text(card).lower()
    reasons: list[str] = []

    if "exact product match" in text or "direct search match" in text:
        reasons.append("exact product match")
    if "walmart cash" in text or "cashrewards" in text:
        reasons.append("Walmart Cash signal")
    if "coupon from api" in text:
        reasons.append("coupon signal")
    if "rollback" in text:
        reasons.append("rollback signal")
    if "clearance" in text:
        reasons.append("clearance signal")
    if "flip/value lead" in text or "rough spread" in text or "margin" in text:
        reasons.append("flip/value spread")
    if "stock: **available" in text or "available online" in text:
        reasons.append("availability signal")
    if _discount(card) >= 20:
        reasons.append(f"{_discount(card):.0f}% trusted markdown")
    if getattr(card, "manual_share_allowed", False):
        reasons.append("manual scout allowed")

    if not reasons:
        reasons.append("API-backed scout lead")
    return reasons[:6]


def scout_rank(card: Any) -> int:
    text = _text(card).lower()
    score = max(_base_score(card), 0)

    if "exact product match" in text or "direct search match" in text:
        score += 30
    if "walmart cash" in text or "cashrewards" in text:
        score += 22
    if "coupon from api" in text:
        score += 14
    if "rollback" in text:
        score += 10
    if "clearance" in text:
        score += 10
    if "flip/value lead" in text or "rough spread" in text or "margin" in text:
        score += 18
    if "stock: **available" in text or "available online" in text:
        score += 8

    price = _price(card)
    if price >= 10:
        score += 6
    elif 0 < price < 3:
        score -= 12

    # Keep this bounded so it reads like a confidence/priority grade.
    return max(0, min(150, int(score)))


def polish_public_scout_card(card: Any, *, rank: int, min_discount: int, position: int) -> Any:
    setattr(card, "score", max(_base_score(card), rank))
    setattr(card, "should_alert", True)

    key = _dedupe_key(card)
    price = _price(card)
    setattr(card, "public_post_key", f"scout:{key}:price:{price:.2f}:rank:{rank}")

    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return card

    if embed.title and not str(embed.title).startswith(("🟨", "🔥", "🚨")):
        embed.title = f"🟨 Scout #{position} • {embed.title}"[:256]

    existing_names = {str(field.name or "") for field in getattr(embed, "fields", []) or []}
    reasons = scout_reasons(card)

    if SCOUT_GRADE_FIELD not in existing_names:
        embed.add_field(
            name=SCOUT_GRADE_FIELD,
            value=(
                f"Rank: **{rank}/150** • Strict verified markdown threshold: **{min_discount}%+**\n"
                f"Why it surfaced: {', '.join(reasons)}\n"
                "Lane: **Scout**, not Verified. This beats silence during Walmart sale weeks without pretending weak API proof is guaranteed."
            ),
            inline=False,
        )

    if BUY_CHECK_FIELD not in existing_names:
        embed.add_field(
            name=BUY_CHECK_FIELD,
            value=(
                "Before buying/posting, confirm:\n"
                "1. Walmart app price matches the card\n"
                "2. Selected color/size/condition is the discounted option\n"
                "3. Seller + shipping are acceptable\n"
                "4. Stock/add-to-cart still works\n"
                "5. Quick comps support resale/value"
            ),
            inline=False,
        )

    embed.set_footer(text=SCOUT_FOOTER)
    return card


def select_best_public_scout_cards(cards: list[Any], *, limit: int = 3, min_discount: int = 50, min_rank: int = 45) -> list[Any]:
    ranked: list[tuple[int, Any]] = []
    seen: set[str] = set()

    for card in cards:
        price = _price(card)
        if price <= 0:
            continue

        key = _dedupe_key(card)
        if key in seen:
            continue
        seen.add(key)

        rank = scout_rank(card)
        if rank < int(min_rank):
            continue

        ranked.append((rank, card))

    ranked.sort(key=lambda item: item[0], reverse=True)

    selected: list[Any] = []
    for position, (rank, card) in enumerate(ranked[: max(1, int(limit))], start=1):
        selected.append(polish_public_scout_card(card, rank=rank, min_discount=min_discount, position=position))
    return selected
