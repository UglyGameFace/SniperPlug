from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.public_deal_quality import (
    LANE_OPEN_BOX_LIKE_NEW,
    LANE_RESTORED_REFURBISHED,
    normalized_lane,
)


class DealQualityBucket(str, Enum):
    VERIFIED_MARKDOWN = "verified_markdown"
    OPEN_BOX_LIKE_NEW = "open_box_like_new"
    RESTORED_REFURBISHED = "restored_refurbished"
    COUPON_OR_CASH = "coupon_or_cash"
    FLIP_LEAD = "flip_lead"
    REVIEW_ONLY = "review_only"
    VARIANT_RISK = "variant_risk"
    BLOCKED_OR_BAD_MATH = "blocked_or_bad_math"


@dataclass(frozen=True)
class DealQuality:
    bucket: DealQualityBucket
    label: str
    reason: str
    public_postable: bool
    score_bonus: int = 0


def classify_candidate(candidate: SourceCandidate) -> DealQuality:
    attrs = candidate.variant_attributes or {}
    signals = " ".join(candidate.signals or []).lower()
    lane = normalized_lane(candidate)

    if candidate.option_mismatch_warning:
        return DealQuality(DealQualityBucket.VARIANT_RISK, "⚠️ Variant risk", candidate.option_mismatch_warning, False, -40)

    if lane == LANE_OPEN_BOX_LIKE_NEW:
        return DealQuality(
            DealQualityBucket.OPEN_BOX_LIKE_NEW,
            "📦 Open box / like-new",
            "Condition lane with API current/reference math required for public posting.",
            True,
            35,
        )

    if lane == LANE_RESTORED_REFURBISHED:
        return DealQuality(
            DealQualityBucket.RESTORED_REFURBISHED,
            "♻️ Restored / refurbished",
            "Restored/refurbished lane with API current/reference math required for public posting.",
            True,
            30,
        )

    if attrs.get("marketplaceCompPrice"):
        return DealQuality(
            DealQualityBucket.FLIP_LEAD,
            "📈 Flip lead",
            "Marketplace comp exists, but it is not Walmart markdown proof.",
            False,
            25,
        )

    if attrs.get("couponSavings") or attrs.get("walmartCashSavings"):
        return DealQuality(
            DealQualityBucket.COUPON_OR_CASH,
            "💵 Coupon/Cash lead",
            "API returned coupon or Walmart Cash value; checkout terms still need review.",
            False,
            18,
        )

    if attrs.get("referencePriceTrusted") == "yes" and candidate.current_price is not None and candidate.typical_price and candidate.typical_price > candidate.current_price:
        return DealQuality(
            DealQualityBucket.VERIFIED_MARKDOWN,
            "✅ Verified markdown",
            "Trusted Walmart reference price is higher than current price.",
            True,
            40,
        )

    if "ignored suspicious" in signals or attrs.get("referencePriceTrusted") == "no":
        return DealQuality(
            DealQualityBucket.BLOCKED_OR_BAD_MATH,
            "❌ Bad math blocked",
            "Suspicious/weak value was blocked from discount proof.",
            False,
            -30,
        )

    return DealQuality(
        DealQualityBucket.REVIEW_ONLY,
        "🟨 Review-only lead",
        "Current price may be interesting, but trusted discount proof did not pass.",
        False,
        0,
    )


def classify_card(card: DealCard) -> DealQuality:
    lane = normalized_lane(card)
    text = str(card.embed.to_dict()).lower()
    if "wrong option" in text or "variant" in text and "mismatch" in text:
        return DealQuality(DealQualityBucket.VARIANT_RISK, "⚠️ Variant risk", "Card contains variant mismatch risk.", False, -40)
    if lane == LANE_OPEN_BOX_LIKE_NEW:
        return DealQuality(DealQualityBucket.OPEN_BOX_LIKE_NEW, "📦 Open box / like-new", "Card is a condition markdown lane.", True, 35)
    if lane == LANE_RESTORED_REFURBISHED:
        return DealQuality(DealQualityBucket.RESTORED_REFURBISHED, "♻️ Restored / refurbished", "Card is a restored/refurbished markdown lane.", True, 30)
    if "marketplace comp" in text or "flip estimate" in text:
        return DealQuality(DealQualityBucket.FLIP_LEAD, "📈 Flip lead", "Card includes marketplace comp / flip context.", False, 25)
    if "walmart cash" in text or "coupon" in text:
        return DealQuality(DealQualityBucket.COUPON_OR_CASH, "💵 Coupon/Cash lead", "Card includes coupon or Walmart Cash value.", False, 18)
    if getattr(card, "should_alert", False) and float(getattr(card, "discount", 0.0) or 0.0) > 0:
        return DealQuality(DealQualityBucket.VERIFIED_MARKDOWN, "✅ Verified markdown", "Card passed verified discount alert rules.", True, 40)
    if "ignored suspicious" in text or "bad math" in text:
        return DealQuality(DealQualityBucket.BLOCKED_OR_BAD_MATH, "❌ Bad math blocked", "Card blocked suspicious value math.", False, -30)
    return DealQuality(DealQualityBucket.REVIEW_ONLY, "🟨 Review-only lead", "Card is private review-only.", False, 0)


def quality_summary(cards: list[DealCard]) -> str:
    counts: dict[DealQualityBucket, int] = {}
    for card in cards:
        quality = classify_card(card)
        counts[quality.bucket] = counts.get(quality.bucket, 0) + 1
    if not counts:
        return "No classified opportunities."
    ordered = [
        DealQualityBucket.VERIFIED_MARKDOWN,
        DealQualityBucket.OPEN_BOX_LIKE_NEW,
        DealQualityBucket.RESTORED_REFURBISHED,
        DealQualityBucket.FLIP_LEAD,
        DealQualityBucket.COUPON_OR_CASH,
        DealQualityBucket.REVIEW_ONLY,
        DealQualityBucket.VARIANT_RISK,
        DealQualityBucket.BLOCKED_OR_BAD_MATH,
    ]
    labels = {
        DealQualityBucket.VERIFIED_MARKDOWN: "verified",
        DealQualityBucket.OPEN_BOX_LIKE_NEW: "open box",
        DealQualityBucket.RESTORED_REFURBISHED: "restored/refurb",
        DealQualityBucket.FLIP_LEAD: "flip",
        DealQualityBucket.COUPON_OR_CASH: "coupon/cash",
        DealQualityBucket.REVIEW_ONLY: "review",
        DealQualityBucket.VARIANT_RISK: "variant risk",
        DealQualityBucket.BLOCKED_OR_BAD_MATH: "blocked math",
    }
    return " • ".join(f"{labels[bucket]}: **{counts[bucket]}**" for bucket in ordered if counts.get(bucket))
