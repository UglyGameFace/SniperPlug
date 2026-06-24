from __future__ import annotations

from typing import Any

import discord


PUBLIC_DEAL_LANE_FIELD = "✅ Public deal lane"
PUBLIC_SCOUT_LANE_FIELD = "🧪 Private scout/review lane"


def card_text(card: Any, *, source_label: str = "") -> str:
    parts: list[str] = [
        str(source_label or ""),
        str(getattr(card, "label", "") or ""),
        str(getattr(card, "url", "") or ""),
    ]
    embed = getattr(card, "embed", None)
    if embed is not None:
        parts.append(str(getattr(embed, "title", "") or ""))
        parts.append(str(getattr(embed, "description", "") or ""))
        for field in getattr(embed, "fields", []) or []:
            parts.append(str(getattr(field, "name", "") or ""))
            parts.append(str(getattr(field, "value", "") or ""))
    return " ".join(parts)


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_zero(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def is_review_or_watchlist(card: Any, *, source_label: str = "") -> bool:
    text = card_text(card, source_label=source_label).lower()
    blocked_terms = (
        "watchlist",
        "review-only",
        "review only",
        "private only",
        "staff review",
        "scout lead",
        "public scout lane",
        "private scout",
        "not verified",
        "not a verified",
        "not blind-buy",
        "not deal proof",
        "shown even without walmart markdown proof",
    )
    return any(term in text for term in blocked_terms)


def has_low_trust_reference(card: Any, *, source_label: str = "") -> bool:
    text = card_text(card, source_label=source_label).lower()
    blocked_terms = (
        "ignored reference",
        "ignored suspicious",
        "ignored low-confidence",
        "weak reference",
        "weak/ignored reference",
        "low-trust/suspicious",
        "blocked as low-trust",
        "reference match: blocked",
        "msrp",
    )
    return any(term in text for term in blocked_terms)


def has_real_price(card: Any) -> bool:
    return float_or_none(getattr(card, "current_price", None)) is not None


def has_verified_api_threshold_discount(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    """
    The only public deal gate.

    Public deals must satisfy the server threshold using trusted API markdown.
    Walmart Cash, coupons, buy-more promos, exact search matches, scores, and
    comp links may be useful diagnostics, but they do not bypass the threshold.
    """
    if is_review_or_watchlist(card, source_label=source_label):
        return False
    if has_low_trust_reference(card, source_label=source_label):
        return False
    if not has_real_price(card):
        return False

    discount = float_or_none(getattr(card, "discount", None)) or 0.0
    return discount >= max(1, int(min_discount))


def is_public_deal_candidate(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    return has_verified_api_threshold_discount(card, source_label=source_label, min_discount=min_discount)


def prepare_public_deal_candidate(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    if not is_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
        return False

    setattr(card, "should_alert", True)

    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and not any(str(field.name or "") == PUBLIC_DEAL_LANE_FIELD for field in embed.fields):
        discount = float_or_none(getattr(card, "discount", None)) or 0.0
        embed.add_field(
            name=PUBLIC_DEAL_LANE_FIELD,
            value=(
                f"Posted because Walmart API trusted markdown is **{discount:.0f}%**, "
                f"meeting this server's **{int(min_discount)}%+** public deal threshold. "
                "No Scout, watchlist, MSRP-only, score-only, or comp-link bypass was used."
            ),
            inline=False,
        )

    return True


def is_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 95) -> bool:
    # Public Scout Lane is intentionally disabled.
    # Review/scout cards may be useful diagnostics, but they are not public deals.
    return False


def prepare_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 95) -> bool:
    return False


def select_public_deal_candidates(cards: list[Any], *, source_label: str = "", min_discount: int = 50, limit: int = 5) -> list[Any]:
    selected: list[Any] = []
    for card in cards:
        if prepare_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
            selected.append(card)
        if len(selected) >= max(1, int(limit)):
            break
    return selected
