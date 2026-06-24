from __future__ import annotations

from typing import Any

import discord

from sniperplug.services.scout_lane_polish import scout_rank


PUBLIC_DEAL_LANE_FIELD = "✅ Public deal lane"
PUBLIC_SCOUT_LANE_FIELD = "🟨 Public scout lane"


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
    bad_terms = (
        "watchlist",
        "review-only",
        "review only",
        "private only",
        "staff review",
        "scout lead",
        "not alertable",
        "weak reference",
        "low-trust/suspicious",
    )
    return any(term in text for term in bad_terms)


def has_walmart_cash_signal(card: Any, *, source_label: str = "") -> bool:
    text = card_text(card, source_label=source_label).lower()
    return "walmart cash" in text or "cashrewards" in text or "cash rewards" in text


def has_real_price(card: Any) -> bool:
    return float_or_none(getattr(card, "current_price", None)) is not None or "$" in card_text(card)


def is_public_deal_candidate(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    """True only for rows that deserve public posting/cache as real deal candidates."""
    if is_review_or_watchlist(card, source_label=source_label):
        return False

    discount = float_or_none(getattr(card, "discount", None)) or 0.0
    score = int_or_zero(getattr(card, "score", 0))

    if has_walmart_cash_signal(card, source_label=source_label) and has_real_price(card) and score >= 80:
        return True

    if discount >= max(1, int(min_discount)) and has_real_price(card):
        return True

    return False


def prepare_public_deal_candidate(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    """Mark real public candidates alertable so threshold actually works."""
    if not is_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
        return False

    setattr(card, "should_alert", True)
    try:
        card.score = max(int(getattr(card, "score", 0) or 0), 90)
    except Exception:
        setattr(card, "score", 90)

    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and not any(str(field.name or "") == PUBLIC_DEAL_LANE_FIELD for field in embed.fields):
        discount = float_or_none(getattr(card, "discount", None)) or 0.0
        if has_walmart_cash_signal(card, source_label=source_label):
            reason = "Walmart Cash / extra value signal detected. Verify amount and selected option before buying."
        else:
            reason = f"Verified markdown is **{discount:.0f}%+**, meeting this server's public deal threshold."
        embed.add_field(
            name=PUBLIC_DEAL_LANE_FIELD,
            value=reason,
            inline=False,
        )

    return True


def is_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 45) -> bool:
    """Allow clearly labeled review/scout leads to post without calling them verified deals."""
    if not has_real_price(card):
        return False

    score = max(int_or_zero(getattr(card, "score", 0)), scout_rank(card))
    text = card_text(card, source_label=source_label).lower()
    manual_allowed = bool(getattr(card, "manual_share_allowed", False))
    scout_text = any(
        term in text
        for term in (
            "exact product match",
            "flip/value lead",
            "review candidate",
            "watchlist",
            "walmart cash",
            "cashrewards",
            "rollback",
            "clearance",
        )
    )
    return score >= int(min_score) and (manual_allowed or scout_text)


def prepare_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 45) -> bool:
    if not is_public_scout_candidate(card, source_label=source_label, min_score=min_score):
        return False

    setattr(card, "should_alert", True)
    try:
        card.score = max(int(getattr(card, "score", 0) or 0), 90)
    except Exception:
        setattr(card, "score", 90)

    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and not any(str(field.name or "") == PUBLIC_SCOUT_LANE_FIELD for field in embed.fields):
        embed.add_field(
            name=PUBLIC_SCOUT_LANE_FIELD,
            value=(
                "Posted because strict verified markdown proof found **0** public deals, "
                "but this was one of the strongest Walmart Scout leads. "
                "**Verify app price, selected option, seller, shipping, stock, and comps before buying.**"
            ),
            inline=False,
        )
    return True


def select_public_deal_candidates(cards: list[Any], *, source_label: str = "", min_discount: int = 50, limit: int = 5) -> list[Any]:
    selected: list[Any] = []
    for card in cards:
        if prepare_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
            selected.append(card)
        if len(selected) >= max(1, int(limit)):
            break
    return selected
