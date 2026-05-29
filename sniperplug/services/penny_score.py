from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.candidate import SourceCandidate


@dataclass(frozen=True)
class PennyScore:
    score: int
    level: str
    reasons: tuple[str, ...]


# Price endings are weak hints only. They should never create a clearance label
# by themselves because normal Home Depot prices often end in .00.
PENNY_PRICE_ENDING_POINTS = {
    "01": 45,
    "02": 35,
    "03": 30,
    "04": 18,
    "06": 8,
}

NEGATIVE_OR_NEUTRAL_ENDINGS = {"00", "98", "99", "97", "95"}

CLEARANCE_KEYWORDS = (
    "clearance",
    "closeout",
    "overstock",
    "yellow tag",
    "discontinued",
)

PROMO_KEYWORDS = (
    "special buy",
    "rollback",
    "memorial day",
    "savings",
    "sale",
)

PENNY_FRIENDLY_CATEGORIES = (
    "faucet",
    "vanity",
    "lighting",
    "ceiling fan",
    "fan",
    "tool",
    "milwaukee",
    "ryobi",
    "ridgid",
    "patio",
    "garden",
    "fixture",
    "sink",
)


def score_penny_candidate(candidate: SourceCandidate, *, has_store_id: bool = False, has_seed: bool = False) -> PennyScore:
    """Score a Home Depot penny/clearance lead conservatively.

    This score is proof-based, not vibes-based. A normal discounted product with
    a .00 price can be a decent deal, but it should not be labeled as a penny or
    clearance watch unless Home Depot gives stronger clearance proof.
    """
    score = 0
    reasons: list[str] = []
    attrs = candidate.variant_attributes or {}
    text = " ".join([candidate.title, *candidate.signals, *attrs.values()]).lower()
    has_zip_anchor = any(signal.lower().startswith("zip:") for signal in candidate.signals)
    has_local_anchor = has_store_id or has_zip_anchor

    ending = price_ending(candidate.current_price)
    if ending in PENNY_PRICE_ENDING_POINTS:
        points = PENNY_PRICE_ENDING_POINTS[ending]
        score += points
        reasons.append(f"Watch price ending .{ending}: +{points}")
    elif ending in NEGATIVE_OR_NEUTRAL_ENDINGS:
        reasons.append(f"Normal/common price ending .{ending}: +0")

    if any(keyword in text for keyword in CLEARANCE_KEYWORDS):
        score += 30
        reasons.append("Explicit clearance/discontinued signal: +30")
    elif any(keyword in text for keyword in PROMO_KEYWORDS):
        score += 8
        reasons.append("Promo/sale signal, not clearance proof: +8")

    if has_seed:
        score += 20
        reasons.append("Saved in clearance seed bank: +20")

    savings_percent = savings_percent_from_candidate(candidate)
    if savings_percent is not None:
        if savings_percent >= 70:
            score += 25
            reasons.append(f"Extreme markdown {savings_percent:.0f}% off: +25")
        elif savings_percent >= 50:
            score += 15
            reasons.append(f"Large markdown {savings_percent:.0f}% off: +15")
        elif savings_percent >= 30:
            score += 8
            reasons.append(f"Moderate markdown {savings_percent:.0f}% off: +8")
        else:
            reasons.append(f"Small markdown {savings_percent:.0f}% off: +0")
    elif attrs.get("price_saving") or attrs.get("percentage_off"):
        score += 5
        reasons.append("Home Depot savings text exists but was price not proven: +5")

    if has_local_anchor:
        # Store ID is strongest, but ZIP is still real local search proof. It must
        # not be treated as raw/no-proof fallback or paid SerpApi credits get wasted.
        local_points = 14 if has_store_id else 12
        score += local_points
        reasons.append(f"Local store/ZIP search: +{local_points}")
    else:
        score -= 25
        reasons.append("No store_id or ZIP supplied: -25")

    if candidate.sku or candidate.product_id or candidate.upc:
        score += 6
        reasons.append("Product ID/SKU present: +6")
    else:
        score -= 15
        reasons.append("No SKU/product ID: -15")

    if candidate.current_price is None:
        score -= 35
        reasons.append("No current price proof: -35")
    elif candidate.current_price < 1:
        score += 20
        reasons.append("Price under $1: +20")
    elif candidate.current_price < 5:
        score += 10
        reasons.append("Price under $5: +10")

    if any(keyword in text for keyword in PENNY_FRIENDLY_CATEGORIES):
        score += 6
        reasons.append("Known Home Depot deal-friendly category: +6")

    score = max(0, min(100, score))
    if score >= 80:
        level = "high_priority_in_store_verification"
    elif score >= 60:
        level = "strong_clearance_candidate"
    elif score >= 40:
        level = "clearance_watch"
    elif score >= 20:
        level = "deal_watch"
    else:
        level = "weak_lead"

    return PennyScore(score=score, level=level, reasons=tuple(reasons[:8]))


def savings_percent_from_candidate(candidate: SourceCandidate) -> float | None:
    if candidate.current_price is None or not candidate.typical_price:
        return None
    if candidate.typical_price <= candidate.current_price:
        return None
    return ((candidate.typical_price - candidate.current_price) / candidate.typical_price) * 100


def price_ending(price: float | None) -> str | None:
    if price is None:
        return None
    cents = int(round((price - int(price)) * 100))
    if cents < 0 or cents > 99:
        return None
    return f"{cents:02d}"
