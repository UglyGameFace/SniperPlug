from __future__ import annotations

from typing import Any

import discord

from sniperplug.services.walmart_cash import walmart_cash_amount_is_sane


SCOUT_GRADE_FIELD = "🧭 Review status"
BUY_CHECK_FIELD = "✅ Quick check"
WALMART_CASH_FIELD = "💵 Walmart Cash"
SCOUT_FOOTER = "Private review lead • Not a verified deal • Never auto-post as a deal"

WEAK_REFERENCE_TERMS = (
    "ignored reference",
    "weak/ignored reference",
    "low-trust/suspicious",
    "blocked as low-trust",
    "reference match: blocked",
    "bad value rejected",
    "review-only",
    "review only",
    "not deal proof",
)

PRIVATE_VALUE_TERMS = (
    "walmart cash",
    "cashrewards",
    "cash rewards",
    "coupon from api",
    "walmart api savings",
    "walmart api promo",
    "api promo cap",
    "buy more",
    "save up",
    "rough spread",
    "flip/value lead",
    "profit",
    "margin",
    "verified markdown",
    "trusted markdown",
    "you save",
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
        title = str(embed.title)
        for prefix in ("🟨 High-confidence Scout", "🟨 Watchlist", "🔥", "🚨"):
            if title.startswith(prefix):
                title = title.split("•", 1)[-1].strip()
        return title
    return str(getattr(card, "label", "") or "Walmart review lead")


def _price(card: Any) -> float:
    try:
        return float(getattr(card, "current_price", None) or 0)
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


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _variant_attributes(card: Any) -> dict[str, Any]:
    attrs = getattr(card, "variant_attributes", None)
    return attrs if isinstance(attrs, dict) else {}


def _confirmed_walmart_cash(card: Any) -> float | None:
    """Return only structured, explicitly proven Walmart Cash."""
    attrs = _variant_attributes(card)
    amount = _float_or_none(attrs.get("walmartCashSavings"))
    proof = str(attrs.get("walmartCashApiProof") or attrs.get("cashAmountConfirmed") or "").strip().lower()
    if proof not in {"yes", "true", "1"}:
        return None
    price = _price(card)
    return amount if walmart_cash_amount_is_sane(amount, current_price=price or None) else None


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
    if _confirmed_walmart_cash(card) is not None:
        return True
    if _discount(card) >= 25 and not has_weak_reference_warning(card):
        return True
    return any(term in text for term in PRIVATE_VALUE_TERMS)


def scout_reasons(card: Any, *, min_discount: int = 50) -> list[str]:
    text = _text(card).lower()
    reasons: list[str] = []
    if has_weak_reference_warning(card):
        reasons.append("reference price was not trusted")
    if _discount(card) >= 25 and not has_weak_reference_warning(card):
        reasons.append(f"{_discount(card):.0f}% review markdown signal")
    cash = _confirmed_walmart_cash(card)
    if cash is not None:
        reasons.append(f"confirmed ${cash:,.2f} Walmart Cash")
    elif "walmart cash" in text or "cashrewards" in text or "cash rewards" in text:
        reasons.append("unconfirmed Walmart Cash wording")
    if "coupon from api" in text:
        reasons.append("API coupon signal")
    if "walmart api savings" in text or "walmart api promo" in text or "buy more" in text or "save up" in text:
        reasons.append("Walmart API promo signal")
    if "rough spread" in text or "flip/value lead" in text or "profit" in text or "margin" in text:
        reasons.append("comparison/value signal")
    if "stock: **available" in text or "available online" in text:
        reasons.append("available now")
    if not reasons:
        reasons.append("no verified discount proof")
    return reasons[:3]


def scout_rank(card: Any, *, min_discount: int = 50) -> int:
    """Rank private review usefulness, never public deal confidence."""
    text = _text(card).lower()
    score = max(_base_score(card), 0)

    if _discount(card) >= 25 and not has_weak_reference_warning(card):
        score += 50
    if _confirmed_walmart_cash(card) is not None:
        score += 30
    elif "walmart cash" in text or "cashrewards" in text or "cash rewards" in text:
        score += 30
    if "coupon from api" in text:
        score += 22
    if "walmart api savings" in text or "walmart api promo" in text or "api promo cap" in text or "buy more" in text or "save up" in text:
        score += 28
    if "rough spread" in text or "flip/value lead" in text or "profit" in text or "margin" in text:
        score += 28
    if "you save" in text and not has_weak_reference_warning(card):
        score += 18
    if "exact product match" in text or "direct search match" in text:
        score += 5
    if "stock: **available" in text or "available online" in text:
        score += 5

    if has_weak_reference_warning(card):
        score -= 60
    if not has_hard_value_signal(card, min_discount=min_discount):
        score -= 25

    price = _price(card)
    if 0 < price < 3:
        score -= 15

    return max(0, min(150, int(score)))


def is_high_confidence_public_scout(card: Any, *, min_discount: int = 50, min_rank: int = 95) -> bool:
    return False


def _compact_description(embed: discord.Embed) -> str:
    description = " ".join(str(embed.description or "").split())
    if len(description) > 260:
        description = description[:257].rstrip() + "…"
    return description


def polish_public_scout_card(card: Any, *, rank: int, min_discount: int, position: int) -> Any:
    """Turn a review candidate into a truthful, compact private card.

    The caller-provided rank is deliberately ignored. Presentation code must
    never promote a weak candidate by forcing it to the old 95-point minimum.
    """
    actual_rank = scout_rank(card, min_discount=min_discount)
    setattr(card, "score", actual_rank)
    setattr(card, "should_alert", False)
    setattr(card, "deal_lane", "private_review")

    key = _dedupe_key(card)
    price = _price(card)
    setattr(card, "public_post_key", f"private-review:{key}:price:{price:.2f}:rank:{actual_rank}")

    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return card

    original_title = _title(card)
    embed.title = f"🟨 Watchlist review #{position} • {original_title}"[:256]
    embed.description = _compact_description(embed)
    embed.clear_fields()

    price_line = f"**${price:,.2f}**" if price > 0 else "Price unavailable"
    embed.add_field(
        name="💵 Walmart price",
        value=f"{price_line}\nPublic deal requirement: **{int(min_discount)}%+ verified markdown**",
        inline=False,
    )

    cash = _confirmed_walmart_cash(card)
    if cash is not None:
        net = price - cash if price > 0 else None
        net_line = f"\nEffective after reward: **${max(0.0, net):,.2f}**" if net is not None else ""
        embed.add_field(
            name=WALMART_CASH_FIELD,
            value=(
                f"Eligible reward: **${cash:,.2f} Walmart Cash**{net_line}\n"
                "Proof: **confirmed from Walmart API/product-page data**. Eligibility can still depend on account and offer terms."
            ),
            inline=False,
        )

    reasons = ", ".join(scout_reasons(card, min_discount=min_discount))
    embed.add_field(
        name=SCOUT_GRADE_FIELD,
        value=(
            f"Private review score: **{actual_rank}/150**\n"
            "Status: **Not a verified deal** — not a verified markdown deal.\n"
            f"Why it was retained for review: {reasons}."
        ),
        inline=False,
    )

    embed.add_field(
        name=BUY_CHECK_FIELD,
        value=(
            "1. Confirm the Walmart app price, Cash offer, and exact option\n"
            "2. Confirm seller, shipping, stock, and offer terms\n"
            "3. Buy only if your own comparison proves the value"
        ),
        inline=False,
    )

    embed.set_footer(text=SCOUT_FOOTER)
    return card


def select_best_public_scout_cards(cards: list[Any], *, limit: int = 3, min_discount: int = 50, min_rank: int = 95) -> list[Any]:
    return []
