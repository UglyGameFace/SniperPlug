from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.deal import NormalizedDeal


@dataclass(frozen=True)
class DealIntentAssessment:
    """Human-readable intent classification for a deal candidate.

    This is not proof that a deal is valid. It helps SniperPlug explain why an
    opportunity looks valuable without labeling every deep discount as a glitch.
    """

    primary_intent: str
    labels: tuple[str, ...]
    public_notes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    score_boost: int = 0
    confidence_adjustment: int = 0
    staff_review_recommended: bool = False


CLOSEOUT_TERMS: tuple[str, ...] = (
    "closeout",
    "clearance",
    "special buy",
    "special/closeout",
    "older production date",
    "older stock",
    "discontinued",
    "final sale",
    "outlet",
    "overstock",
    "open box",
    "open-box",
    "refurbished",
    "scratch and dent",
    "last chance",
)

VENDOR_PROMO_TERMS: tuple[str, ...] = (
    "manufacturer rebate",
    "rebate",
    "instant savings",
    "vendor promo",
    "supplier promo",
    "coupon",
    "promo code",
    "promotion",
    "rollback",
    "member price",
    "membership price",
    "bundle offer",
    "sale price",
)

PRICE_ERROR_TERMS: tuple[str, ...] = (
    "price error",
    "pricing error",
    "glitch",
    "cart price",
    "checkout price",
    "wrong price",
    "$0",
    "$0.00",
    "$0.01",
    "100% off",
    "impossible price",
)

FITMENT_TERMS: tuple[str, ...] = (
    "tire",
    "tires",
    "fitment",
    "vehicle specific",
    "vehicle-specific",
    "per tire",
    "load range",
    "serv. desc",
    "utqg",
    "production year",
    "wheel not included",
    "brake rotor",
    "brake pad",
    "spark plug",
    "oem part",
    "235/",
    "245/",
    "255/",
    "265/",
    "275/",
    "r17",
    "r18",
    "r19",
    "r20",
    "r21",
    "r22",
)

QUANTITY_TERMS: tuple[str, ...] = (
    "set of",
    "pack of",
    "case of",
    "bundle",
    "multi-pack",
    "multipack",
    "per tire",
    "per unit",
    "qty",
    "quantity discount",
    "case pack",
    "bulk pack",
)

ACCOUNT_SPECIFIC_TERMS: tuple[str, ...] = (
    "business account",
    "business price",
    "membership",
    "member only",
    "member-only",
    "ymmv",
    "targeted",
    "account specific",
    "account-specific",
    "prime only",
)


def assess_deal_intent(deal: NormalizedDeal) -> DealIntentAssessment:
    text = _deal_text(deal)

    closeout_hits = _hits(text, CLOSEOUT_TERMS)
    vendor_hits = _hits(text, VENDOR_PROMO_TERMS)
    price_error_hits = _hits(text, PRICE_ERROR_TERMS)
    fitment_hits = _hits(text, FITMENT_TERMS)
    quantity_hits = _hits(text, QUANTITY_TERMS)
    account_hits = _hits(text, ACCOUNT_SPECIFIC_TERMS)

    ratio = _price_ratio(deal)
    near_zero = deal.current_price is not None and deal.current_price <= 1
    very_low_for_value = bool(deal.current_price is not None and deal.current_price <= 10 and (deal.typical_price or 0) >= 100)
    unexplained_extreme_discount = bool(ratio is not None and ratio <= 0.10 and not closeout_hits and not vendor_hits)

    labels: list[str] = []
    public_notes: list[str] = []
    risk_flags: list[str] = []
    score_boost = 0
    confidence_adjustment = 0
    staff_review = False

    if near_zero or very_low_for_value or price_error_hits or unexplained_extreme_discount:
        primary_intent = "possible_price_error"
        labels.append("⚠️ Possible Price Error")
        public_notes.append("Price looks unusually low; verify final cart/checkout price before buying.")
        risk_flags.append("Possible price error or checkout mismatch")
        score_boost += 42
        confidence_adjustment -= 8
        staff_review = True
    elif closeout_hits:
        primary_intent = "legit_closeout_or_clearance"
        labels.append("🏷️ Closeout / Clearance")
        public_notes.append("Retailer wording suggests an intentional closeout, clearance, older-stock, or discontinued-item markdown.")
        risk_flags.append("Closeout availability can disappear quickly")
        score_boost += 24 if ratio is not None and ratio <= 0.50 else 12
        confidence_adjustment += 8
    elif vendor_hits:
        primary_intent = "supplier_or_vendor_promo"
        labels.append("🏭 Supplier / Vendor Promo")
        public_notes.append("Deal may be an intentional supplier, vendor, rebate, coupon, rollback, or member-price promotion.")
        risk_flags.append("Verify coupon, rebate, membership, and checkout terms")
        score_boost += 14
        confidence_adjustment += 5
    elif ratio is not None and ratio <= 0.25:
        primary_intent = "unexplained_deep_discount"
        labels.append("🔎 Unexplained Deep Discount")
        public_notes.append("Deep discount found, but no clear closeout or promo explanation was detected.")
        risk_flags.append("Needs source verification before public blast")
        score_boost += 26
        confidence_adjustment -= 5
        staff_review = True
    else:
        primary_intent = "standard_deal_candidate"

    if fitment_hits:
        labels.append("🚗 Fitment-Sensitive")
        public_notes.append("Verify exact model, size, fitment, compatibility, and install requirements.")
        risk_flags.append("Fitment-sensitive item; wrong size/model can make the deal useless")
        score_boost += 6

    if quantity_hits:
        labels.append("📦 Quantity / Pack Check")
        public_notes.append("Verify unit count, pack size, quantity limits, and per-item price.")
        risk_flags.append("Quantity, pack-size, or per-unit price must be checked")
        score_boost += 6

    if deal.requires_business_account or deal.is_ymmv or account_hits:
        labels.append("👤 Account-Specific")
        public_notes.append("Offer may depend on account type, membership, region, or targeted pricing.")
        risk_flags.append("May be account-specific or YMMV")
        score_boost += 4

    return DealIntentAssessment(
        primary_intent=primary_intent,
        labels=tuple(_dedupe(labels)),
        public_notes=tuple(_dedupe(public_notes)),
        risk_flags=tuple(_dedupe(risk_flags)),
        score_boost=score_boost,
        confidence_adjustment=confidence_adjustment,
        staff_review_recommended=staff_review,
    )


def apply_deal_intent_signals(deal: NormalizedDeal) -> NormalizedDeal:
    """Attach intent labels and cautious user-facing notes to a deal."""
    assessment = assess_deal_intent(deal)

    for label in assessment.labels:
        _append_unique(deal.alert_tags, label)
    for flag in assessment.risk_flags:
        _append_unique(deal.risk_flags, flag)
    for note in assessment.public_notes[:3]:
        _append_unique(deal.verification_notes, note)

    if assessment.primary_intent == "possible_price_error":
        deal.is_possible_price_error = True
        deal.risk_level = "high"
    elif assessment.staff_review_recommended and deal.risk_level == "low":
        deal.risk_level = "medium"

    if assessment.confidence_adjustment:
        deal.confidence_score = max(0, min(100, deal.confidence_score + assessment.confidence_adjustment))

    return deal


def _deal_text(deal: NormalizedDeal) -> str:
    values = [
        deal.title,
        deal.retailer,
        deal.availability_message or "",
        deal.condition or "",
        *(deal.risk_flags or []),
        *(deal.alert_tags or []),
        *(deal.verification_notes or []),
    ]
    return " ".join(value.lower() for value in values if value)


def _price_ratio(deal: NormalizedDeal) -> float | None:
    if deal.current_price is None or not deal.typical_price or deal.typical_price <= 0:
        return None
    return deal.current_price / deal.typical_price


def _hits(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for term in terms if term in text)


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
