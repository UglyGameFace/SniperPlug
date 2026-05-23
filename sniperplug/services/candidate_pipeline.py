from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.anomaly_score import AnomalyScore, score_deal_anomaly
from sniperplug.services.risk_flags import apply_risk_flags
from sniperplug.services.routing import RouteDecision, STAFF_REVIEW_ROUTE, choose_primary_route


@dataclass(frozen=True)
class CandidateDecision:
    candidate: SourceCandidate
    deal: NormalizedDeal
    anomaly: AnomalyScore
    route: RouteDecision
    should_alert: bool
    hold_for_review: bool
    reasons: tuple[str, ...]


MIN_PUBLIC_ALERT_SCORE = 90
MIN_REVIEW_SCORE = 60


def evaluate_candidate(candidate: SourceCandidate) -> CandidateDecision:
    """Convert a source-found candidate into an alert/review decision.

    This function is intentionally provider-agnostic. Providers find candidates;
    the pipeline scores, routes, and decides what happens next.
    """
    deal = apply_risk_flags(candidate.to_normalized_deal())
    anomaly = score_deal_anomaly(deal)
    route = choose_primary_route(deal)

    reasons = list(anomaly.reasons)
    should_alert = anomaly.score >= MIN_PUBLIC_ALERT_SCORE
    hold_for_review = MIN_REVIEW_SCORE <= anomaly.score < MIN_PUBLIC_ALERT_SCORE

    condition_label = lower_price_condition_label(deal.condition, deal.variant_attributes)
    if condition_label:
        deal.alert_tags.append("Lower-price condition offer")
        deal.risk_flags.append(f"Condition-specific lower price: {condition_label}")
        deal.verification_notes.append(f"Selected condition: {condition_label}")
        if "selected condition" not in " ".join(reason.lower() for reason in reasons):
            reasons.append(f"Selected condition offer: {condition_label}")
        # Condition-specific prices can be real bangers, but they must not be
        # mislabeled as brand-new retail. Keep them public-alertable only when
        # the base score is strong enough and the condition is clearly shown.
        if anomaly.score >= MIN_PUBLIC_ALERT_SCORE:
            should_alert = True

    if not deal.product_url:
        should_alert = False
        hold_for_review = False
        reasons.append("Rejected: missing product URL")

    if deal.current_price is None:
        should_alert = False
        hold_for_review = True
        reasons.append("Review: missing current price")

    if candidate.can_add_to_cart is False and anomaly.level in {"urgent", "nuclear"}:
        should_alert = False
        hold_for_review = True
        reasons.append("Review: urgent anomaly but add-to-cart was not confirmed")

    if getattr(candidate, "option_mismatch_warning", None) or getattr(deal, "option_mismatch_warning", None):
        should_alert = False
        hold_for_review = True
        route = RouteDecision(STAFF_REVIEW_ROUTE, "selected variant or option needs manual review")
        reasons.append("Review: selected option mismatch")

    return CandidateDecision(
        candidate=candidate,
        deal=deal,
        anomaly=anomaly,
        route=route,
        should_alert=should_alert,
        hold_for_review=hold_for_review,
        reasons=tuple(reasons[:10]),
    )


def lower_price_condition_label(condition: str | None, attrs: dict[str, str]) -> str | None:
    text = " ".join(
        str(value)
        for value in (
            condition,
            attrs.get("condition"),
            attrs.get("offerCondition"),
            attrs.get("conditionGroup"),
            attrs.get("conditionLabel"),
        )
        if value
    ).strip()
    if not text:
        return None
    lowered = text.lower()
    condition_terms = (
        "open box",
        "open-box",
        "openbox",
        "like new",
        "likenew",
        "excellent",
        "certified refurbished",
        "refurbished",
        "renewed",
        "used - like new",
        "used like new",
    )
    if any(term in lowered for term in condition_terms):
        return text
    return None
