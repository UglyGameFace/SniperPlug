from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.candidate import SourceCandidate


@dataclass(frozen=True)
class PennyScore:
    score: int
    level: str
    reasons: tuple[str, ...]


PENNY_PRICE_ENDING_POINTS = {
    "01": 50,
    "02": 40,
    "03": 35,
    "04": 25,
    "06": 15,
    "00": 5,
}

CLEARANCE_KEYWORDS = (
    "clearance",
    "closeout",
    "overstock",
    "special buy",
    "rollback",
    "yellow tag",
    "discontinued",
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
    score = 0
    reasons: list[str] = []

    ending = price_ending(candidate.current_price)
    if ending and ending in PENNY_PRICE_ENDING_POINTS:
        points = PENNY_PRICE_ENDING_POINTS[ending]
        score += points
        reasons.append(f"Price ending .{ending}: +{points}")

    text = " ".join([candidate.title, *candidate.signals]).lower()
    if any(keyword in text for keyword in CLEARANCE_KEYWORDS):
        score += 15
        reasons.append("Clearance-like keyword/signal: +15")
    if any(keyword in text for keyword in PENNY_FRIENDLY_CATEGORIES):
        score += 10
        reasons.append("Penny-friendly Home Depot category/brand: +10")
    if has_seed:
        score += 10
        reasons.append("Saved in clearance seed bank: +10")
    if has_store_id:
        score += 10
        reasons.append("Store-specific search: +10")
    else:
        score -= 30
        reasons.append("No store_id supplied: -30")

    if candidate.sku or candidate.product_id or candidate.upc:
        score += 10
        reasons.append("Product ID/SKU present: +10")
    else:
        score -= 20
        reasons.append("No SKU/product ID: -20")

    if candidate.current_price is None:
        score -= 30
        reasons.append("No local/current price proof: -30")
    elif candidate.current_price < 1:
        score += 20
        reasons.append("Price under $1: +20")
    elif candidate.current_price < 5:
        score += 15
        reasons.append("Price under $5: +15")

    score = max(0, min(100, score))
    if score >= 80:
        level = "high_priority_in_store_verification"
    elif score >= 60:
        level = "strong_penny_candidate"
    elif score >= 30:
        level = "clearance_watch"
    else:
        level = "weak_lead"

    return PennyScore(score=score, level=level, reasons=tuple(reasons[:8]))


def price_ending(price: float | None) -> str | None:
    if price is None:
        return None
    cents = int(round((price - int(price)) * 100))
    if cents < 0 or cents > 99:
        return None
    return f"{cents:02d}"
