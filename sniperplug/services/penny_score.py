from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.candidate import SourceCandidate


@dataclass(frozen=True)
class PennyScore:
    score: int
    level: str
    reasons: tuple[str, ...]


# Price endings are only one signal. They must not turn a normal product into a
# clearance/penny lead by themselves unless the ending is one of the known deep
# markdown endings. .00 is intentionally NOT scored.
PENNY_PRICE_ENDING_POINTS = {
    "01": 80,
    "02": 65,
    "03": 55,
    "04": 45,
    "06": 25,
}

NEUTRAL_PRICE_ENDINGS = {"00", "95", "97", "98", "99"}

CLEARANCE_KEYWORDS = (
    "clearance",
    "closeout",
    "overstock",
    "yellow tag",
    "discontinued",
    "final price",
    "final markdown",
)

SALE_KEYWORDS = (
    "special buy",
    "rollback",
    "sale",
    "savings",
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

    A ZIP-only SerpApi search is local context, but local context + category +
    SKU is not enough to call something a clearance watch. Real confidence must
    come from a markdown ending, explicit clearance/sale proof, or comparison
    price savings.
    """
    score = 0
    reasons: list[str] = []
    attrs = candidate.variant_attributes or {}

    ending = price_ending(candidate.current_price)
    if ending in PENNY_PRICE_ENDING_POINTS:
        points = PENNY_PRICE_ENDING_POINTS[ending]
        score += points
        reasons.append(f"Markdown price ending .{ending}: +{points}")
    elif ending in NEUTRAL_PRICE_ENDINGS:
        reasons.append(f"Price ending .{ending}: neutral, not clearance proof")

    discount_points = _discount_points(candidate)
    if discount_points:
        score += discount_points
        reasons.append(f"Verified comparison-price savings: +{discount_points}")
    elif candidate.current_price is not None and candidate.typical_price is None:
        reasons.append("No comparison price returned; savings cannot be proven")

    text = " ".join([candidate.title, *candidate.signals, *attrs.values()]).lower()
    if any(keyword in text for keyword in CLEARANCE_KEYWORDS):
        score += 25
        reasons.append("Explicit clearance/discontinued signal: +25")
    elif any(keyword in text for keyword in SALE_KEYWORDS):
        score += 12
        reasons.append("Sale/Special Buy signal: +12")

    if attrs.get("price_saving") or attrs.get("percentage_off"):
        score += 8
        reasons.append("Home Depot savings metadata present: +8")

    if has_seed:
        score += 15
        reasons.append("Saved in clearance seed bank: +15")
    if has_store_id:
        score += 5
        reasons.append("Local store/ZIP search: +5")
    else:
        score -= 15
        reasons.append("No store_id or ZIP supplied: -15")

    if candidate.sku or candidate.product_id or candidate.upc:
        score += 5
        reasons.append("Product ID/SKU present: +5")
    else:
        score -= 15
        reasons.append("No SKU/product ID: -15")

    if any(keyword in text for keyword in PENNY_FRIENDLY_CATEGORIES):
        score += 3
        reasons.append("Relevant Home Depot category/brand context: +3")

    if candidate.current_price is None:
        score -= 30
        reasons.append("No current price proof: -30")
    elif candidate.current_price < 1:
        score += 20
        reasons.append("Price under $1: +20")
    elif candidate.current_price < 5:
        score += 10
        reasons.append("Price under $5: +10")

    score = max(0, min(100, score))
    if score >= 85:
        level = "high_priority_in_store_verification"
    elif score >= 65:
        level = "strong_penny_candidate"
    elif score >= 45:
        level = "clearance_candidate"
    elif score >= 25:
        level = "markdown_watch"
    else:
        level = "low_confidence_result"

    return PennyScore(score=score, level=level, reasons=tuple(reasons[:8]))


def _discount_points(candidate: SourceCandidate) -> int:
    if candidate.current_price is None or not candidate.typical_price or candidate.typical_price <= candidate.current_price:
        return 0
    discount_pct = ((candidate.typical_price - candidate.current_price) / candidate.typical_price) * 100
    if discount_pct >= 70:
        return 35
    if discount_pct >= 50:
        return 28
    if discount_pct >= 30:
        return 20
    if discount_pct >= 15:
        return 12
    if discount_pct >= 5:
        return 6
    return 0


def price_ending(price: float | None) -> str | None:
    if price is None:
        return None
    cents = int(round((price - int(price)) * 100))
    if cents < 0 or cents > 99:
        return None
    return f"{cents:02d}"
