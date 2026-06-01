from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.services.deal_search_modes import card_text, category_demand_score, has_popular_brand, seller_trust_score


DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR = 78
CONFIDENCE_FIELD_NAME = "🔥 Public confidence"


@dataclass(frozen=True)
class DealConfidence:
    score: int
    label: str
    reasons: tuple[str, ...]
    cautions: tuple[str, ...] = ()

    @property
    def public_ready(self) -> bool:
        return self.score >= DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR


@dataclass(frozen=True)
class ConfidenceSelection:
    cards: list[Any]
    hidden_low_confidence: int = 0
    floor: int = DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR

    def summary_line(self) -> str:
        return f"confidence-ready: **{len(self.cards)}** • low-confidence hidden: **{self.hidden_low_confidence}** • floor: **{self.floor}/100**"


def score_deal_confidence(card: Any) -> DealConfidence:
    """Score whether a verified deal card is safe/strong enough to auto-post publicly.

    This is direct service code, not a runtime hook. It combines SniperPlug's
    verified deal proof with demand/brand/trust signals and produces a small
    human-readable explanation for the public embed.
    """
    text = card_text(card)
    reasons: list[str] = []
    cautions: list[str] = []
    score = 36.0

    discount = float(getattr(card, "discount", 0.0) or 0.0)
    internal_score = float(getattr(card, "score", 0.0) or 0.0)
    demand = max(0.0, min(24.0, category_demand_score(card) * 0.45))
    trust = seller_trust_score(card)

    if discount >= 70:
        score += 24.0
        reasons.append(f"deep verified markdown ({discount:.0f}% off)")
    elif discount >= 50:
        score += 19.0
        reasons.append(f"strong verified markdown ({discount:.0f}% off)")
    elif discount >= 35:
        score += 14.0
        reasons.append(f"solid verified markdown ({discount:.0f}% off)")
    elif discount > 0:
        score += 8.0
        reasons.append(f"verified markdown ({discount:.0f}% off)")

    if bool(getattr(card, "should_alert", False)):
        score += 10.0
        reasons.append("passed alertable proof")
    elif internal_score >= 90:
        score += 6.0
        reasons.append("high internal proof score")

    score += min(12.0, max(0.0, internal_score) / 12.0)

    if has_popular_brand(card):
        score += 14.0
        reasons.append("recognizable/high-demand brand signal")
    elif demand >= 8:
        score += 7.0
        reasons.append("category demand signal")
    else:
        cautions.append("brand demand is weaker")

    score += demand

    if trust >= 35:
        score += 14.0
        reasons.append("trusted seller/fulfillment signal")
    elif trust >= 10:
        score += 7.0
        reasons.append("availability/seller signal")

    if getattr(card, "current_price", None) is not None or "$" in text:
        score += 4.0
        reasons.append("current price shown")

    if "selected option" in text or "exact product match" in text or getattr(card, "selected_offer_id", None):
        score += 5.0
        reasons.append("variant/option proof present")

    if "walmart cash" in text or "coupon" in text:
        score += 3.0
        reasons.append("extra promo signal")

    low_trust_terms = (
        "third-party seller",
        "marketplace seller",
        "weak reference",
        "staff review",
        "private only",
        "review-only",
        "not alertable",
    )
    if any(term in text for term in low_trust_terms):
        score -= 18.0
        cautions.append("contains lower-trust/review-only signal")

    low_priority_terms = (
        "case only",
        "cover only",
        "filter only",
        "sample",
        "tester",
        "travel size",
        "replacement part",
        "accessory only",
    )
    if any(term in text for term in low_priority_terms):
        score -= 16.0
        cautions.append("possible accessory/sample/replacement item")

    final_score = int(max(0, min(100, round(score))))
    if final_score >= 90:
        label = "Excellent"
    elif final_score >= DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR:
        label = "Public-ready"
    elif final_score >= 65:
        label = "Review first"
    else:
        label = "Too weak"

    return DealConfidence(
        score=final_score,
        label=label,
        reasons=tuple(dedupe_keep_order(reasons)[:5]),
        cautions=tuple(dedupe_keep_order(cautions)[:3]),
    )


def select_confident_public_cards(cards: list[Any], *, floor: int = DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR) -> ConfidenceSelection:
    selected: list[Any] = []
    hidden = 0
    for card in cards:
        confidence = score_deal_confidence(card)
        setattr(card, "public_confidence_score", confidence.score)
        setattr(card, "public_confidence_label", confidence.label)
        if confidence.score >= floor:
            selected.append(annotate_card_with_confidence(card, confidence=confidence))
        else:
            hidden += 1
    return ConfidenceSelection(cards=selected, hidden_low_confidence=hidden, floor=floor)


def annotate_card_with_confidence(card: Any, *, confidence: DealConfidence | None = None) -> Any:
    confidence = confidence or score_deal_confidence(card)
    setattr(card, "public_confidence_score", confidence.score)
    setattr(card, "public_confidence_label", confidence.label)
    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and not has_confidence_field(embed):
        embed.add_field(name=CONFIDENCE_FIELD_NAME, value=format_confidence_value(confidence), inline=False)
    return card


def annotate_cards_with_confidence(cards: list[Any]) -> list[Any]:
    return [annotate_card_with_confidence(card) for card in cards]


def format_confidence_value(confidence: DealConfidence) -> str:
    reason_text = "\n".join(f"✅ {reason}" for reason in confidence.reasons[:4]) or "✅ Verified deal proof passed"
    caution_text = ""
    if confidence.cautions:
        caution_text = "\n" + "\n".join(f"⚠️ {caution}" for caution in confidence.cautions[:2])
    return f"**{confidence.score}/100 — {confidence.label}**\n{reason_text}{caution_text}"


def has_confidence_field(embed: discord.Embed) -> bool:
    return any(str(field.name or "") == CONFIDENCE_FIELD_NAME for field in embed.fields)


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
