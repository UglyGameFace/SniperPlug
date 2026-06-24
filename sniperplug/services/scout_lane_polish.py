from __future__ import annotations

from typing import Any
import re

import discord


SCOUT_GRADE_FIELD = "🚦 SniperPlug Scout Grade"
BUY_CHECK_FIELD = "✅ 20-second buy check"
SCOUT_FOOTER = "Scout Lane: high-confidence lead, not verified proof. Verify before buying."

WEAK_REFERENCE_TERMS = (
    "ignored reference",
    "weak/ignored reference",
    "low-trust/suspicious",
    "blocked as low-trust",
    "reference match: blocked",
    "bad value rejected",
    "review-only",
    "review only",
)

VALUE_PROOF_TERMS = (
    "walmart cash",
    "cashrewards",
    "cash rewards",
    "coupon from api",
    "you save",
    "trusted markdown",
    "verified markdown",
    "rough spread",
    "flip/value lead",
    "profit",
    "margin",
    "ebay sold",
    "comps support",
    "comp research",
    "walmart api savings",
    "walmart api promo",
    "api promo cap",
    "api savings amount",
    "buy more",
    "save up",
)


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


def _dedupe_key(card: Any) -> str:
    for attr in ("selected_offer_id", "sku", "upc", "url", "label"):
        value = getattr(card, attr, None)
        if value:
            return str(value).strip().lower()
    return _title(card).strip().lower()


def has_weak_reference_warning(card: Any) -> bool:
    text = _text(card).lower()
    return any(term in text for term in WEAK_REFERENCE_TERMS)


def has_hard_value_signal(card: Any, *, min_discount: int = 50) -> bool:
    text = _text(card).lower()
    discount = _discount(card)

    if discount >= max(25, int(min_discount) - 10):
        return True

    if "walmart cash" in text or "cashrewards" in text or "cash rewards" in text:
        return True

    if "coupon from api" in text:
        return True

    if "walmart api savings" in text or "walmart api promo" in text or "api promo cap" in text or "buy more" in text or "save up" in text:
        return True

    if "rough spread" in text or "flip/value lead" in text or "profit" in text or "margin" in text:
        return True

    if "you save" in text and not has_weak_reference_warning(card):
        return True

    if ("trusted was/reference" in text or "verified markdown" in text or "trusted markdown" in text) and not has_weak_reference_warning(card):
        return True

    return False


def scout_reasons(card: Any, *, min_discount: int = 50) -> list[str]:
    text = _text(card).lower()
    reasons: list[str] = []

    if _discount(card) >= max(25, int(min_discount) - 10):
        reasons.append(f"{_discount(card):.0f}% markdown signal")
    if "walmart cash" in text or "cashrewards" in text or "cash rewards" in text:
        reasons.append("Walmart Cash value")
    if "coupon from api" in text:
        reasons.append("coupon value")
    if "walmart api savings" in text or "walmart api promo" in text or "api promo cap" in text or "buy more" in text or "save up" in text:
        reasons.append("Walmart API promo/savings")
    if "rough spread" in text or "flip/value lead" in text or "profit" in text or "margin" in text:
        reasons.append("comp/profit spread")
    if "you save" in text and not has_weak_reference_warning(card):
        reasons.append("visible savings proof")
    if "stock: **available" in text or "available online" in text:
        reasons.append("available now")

    if not reasons:
        reasons.append("no hard public value proof")
    return reasons[:6]


def scout_rank(card: Any, *, min_discount: int = 50) -> int:
    text = _text(card).lower()
    score = max(_base_score(card), 0)

    # Hard value signals matter. Vague search words do not.
    if _discount(card) >= max(25, int(min_discount) - 10):
        score += 35
    if "walmart cash" in text or "cashrewards" in text or "cash rewards" in text:
        score += 30
    if "coupon from api" in text:
        score += 22
    if "walmart api savings" in text or "walmart api promo" in text or "api promo cap" in text or "buy more" in text or "save up" in text:
        score += 28
    if "rough spread" in text or "flip/value lead" in text or "profit" in text or "margin" in text:
        score += 28
    if "you save" in text and not has_weak_reference_warning(card):
        score += 18

    # Helpful, but never enough by itself.
    if "exact product match" in text or "direct search match" in text:
        score += 8
    if "rollback" in text:
        score += 5
    if "clearance" in text:
        score += 5
    if "stock: **available" in text or "available online" in text:
        score += 5

    if has_weak_reference_warning(card) and not has_hard_value_signal(card, min_discount=min_discount):
        score -= 45

    price = _price(card)
    if price <= 0:
        score -= 50
    elif 0 < price < 3:
        score -= 15

    return max(0, min(150, int(score)))


def is_high_confidence_public_scout(card: Any, *, min_discount: int = 50, min_rank: int = 95) -> bool:
    # Public Scout Lane is disabled.
    # A Scout/Watchlist lead can be useful, but it is not a public deal.
    # Public posting is locked to trusted API markdown >= the server threshold.
    return False

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
        embed.title = f"🟨 High-confidence Scout #{position} • {embed.title}"[:256]

    existing_names = {str(field.name or "") for field in getattr(embed, "fields", []) or []}
    reasons = scout_reasons(card, min_discount=min_discount)

    if SCOUT_GRADE_FIELD not in existing_names:
        embed.add_field(
            name=SCOUT_GRADE_FIELD,
            value=(
                f"Rank: **{rank}/150** • Public scout minimum: **95/150**\n"
                f"Why it surfaced: {', '.join(reasons)}\n"
                "Lane: **High-confidence Scout**, not Verified. It has a hard value signal, but still needs a final human check."
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


def select_best_public_scout_cards(cards: list[Any], *, limit: int = 3, min_discount: int = 50, min_rank: int = 95) -> list[Any]:
    ranked: list[tuple[int, Any]] = []
    seen: set[str] = set()

    for card in cards:
        key = _dedupe_key(card)
        if key in seen:
            continue
        seen.add(key)

        rank = scout_rank(card, min_discount=min_discount)
        if not is_high_confidence_public_scout(card, min_discount=min_discount, min_rank=min_rank):
            continue

        ranked.append((rank, card))

    ranked.sort(key=lambda item: item[0], reverse=True)

    selected: list[Any] = []
    for position, (rank, card) in enumerate(ranked[: max(1, int(limit))], start=1):
        selected.append(polish_public_scout_card(card, rank=rank, min_discount=min_discount, position=position))
    return selected
