from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.deal_intent import assess_deal_intent
from sniperplug.services.opportunity_watchlist import category_for_title


@dataclass(frozen=True)
class AnomalyScore:
    score: int
    level: str
    reasons: tuple[str, ...]


WEAK_SIGNALS: tuple[str, ...] = (
    "site slow",
    "keep refreshing",
    "twitter hype",
    "people posting",
    "going viral",
    "hurry",
)


def score_deal_anomaly(deal: NormalizedDeal) -> AnomalyScore:
    """
    Scores whether a deal is objectively weird enough to alert.

    Weak social/chaos phrases must never create an alert by themselves. They can
    only add urgency after a real price/product anomaly exists.
    """
    score = 0
    reasons: list[str] = []
    suspicious_reference = has_suspicious_reference(deal)

    category = category_for_title(deal.title)
    if category:
        score += category.demand_level // 2
        reasons.append(f"High-demand category: {category.label}")

        if not suspicious_reference and deal.discount_percent is not None and deal.discount_percent >= category.min_discount_percent:
            score += 45
            reasons.append(f"Discount beats {category.label} threshold")

        if category.absolute_price_floor is not None and deal.current_price is not None:
            if deal.current_price <= category.absolute_price_floor:
                score += 35
                reasons.append(f"Price is below watched floor for {category.label}")

    if deal.current_price is not None and deal.current_price <= 1:
        score += 80
        reasons.append("Extreme near-zero price")
    elif (
        not suspicious_reference
        and deal.current_price is not None
        and deal.current_price <= 10
        and (deal.typical_price or 0) >= 100
    ):
        score += 55
        reasons.append("Very low price for normally expensive item")

    if not suspicious_reference and deal.typical_price and deal.current_price is not None:
        ratio = deal.current_price / deal.typical_price
        if ratio <= 0.1:
            score += 70
            reasons.append("90%+ below typical price")
        elif ratio <= 0.25:
            score += 45
            reasons.append("75%+ below typical price")
        elif ratio <= 0.5:
            score += 25
            reasons.append("50%+ below typical price")
    elif suspicious_reference:
        reasons.append("Reference price ignored because it looked mismatched")

    if deal.coupon_terms:
        score += 35
        reasons.append("Coupon observed")
    if deal.coupon_stack_detected:
        score += 35
        reasons.append("Coupon stack observed")
    if deal.is_subscribe_save:
        score += 15
        reasons.append("Subscribe & Save involved")
    if deal.coupon_savings is not None and deal.coupon_savings >= 2:
        score += min(30, int(deal.coupon_savings * 4))
        reasons.append("Coupon savings observed")
    if deal.coupon_percent is not None and deal.coupon_percent >= 20:
        score += 20
        reasons.append("20%+ coupon impact observed")

    if deal.product_url:
        score += 10
        reasons.append("Product link present")

    if deal.image_url:
        score += 8
        reasons.append("Product image supplied")

    if deal.asin or deal.sku or deal.upc:
        score += 12
        reasons.append("Product identifier present")

    availability = (deal.availability_message or "").lower()
    if "add-to-cart observed" in availability or "checkout price observed" in availability:
        score += 25
        reasons.append("Cart or checkout signal observed")

    if "may require business account" in availability or any("business" in flag.lower() for flag in deal.risk_flags):
        score += 12
        reasons.append("Business-account deal")

    if deal.is_ymmv:
        score += 8
        reasons.append("YMMV deal")

    intent = assess_deal_intent(deal)
    if intent.score_boost:
        score += intent.score_boost
        reasons.append(f"Intent: {intent.primary_intent}")
    if intent.staff_review_recommended:
        reasons.append("Staff review recommended before public blast")

    weak_hits = weak_signal_hits(deal)
    if weak_hits and score >= 80:
        score += min(15, len(weak_hits) * 5)
        reasons.append("Social/site chatter only boosted an existing anomaly")
    elif weak_hits:
        reasons.append("Weak chatter ignored because no strong anomaly exists")

    final_score = max(0, min(250, score))
    return AnomalyScore(score=final_score, level=score_level(final_score), reasons=tuple(reasons[:8]))


def score_level(score: int) -> str:
    if score >= 170:
        return "nuclear"
    if score >= 130:
        return "urgent"
    if score >= 90:
        return "strong"
    if score >= 60:
        return "watch"
    return "ignore"


def has_suspicious_reference(deal: NormalizedDeal) -> bool:
    return any(
        "ignored suspicious" in flag.lower()
        or "reference price looked mismatched" in flag.lower()
        or "reference price needs recheck" in flag.lower()
        for flag in deal.risk_flags
    )


def weak_signal_hits(deal: NormalizedDeal) -> list[str]:
    text = " ".join(
        value.lower()
        for value in [
            deal.title,
            deal.availability_message or "",
            *deal.risk_flags,
            *deal.alert_tags,
        ]
        if value
    )
    return [signal for signal in WEAK_SIGNALS if signal in text]
