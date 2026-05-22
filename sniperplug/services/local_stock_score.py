from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.local_stock import LocalStockOffer


@dataclass(frozen=True)
class LocalStockScore:
    score: int
    level: str
    reasons: tuple[str, ...]


def score_local_stock_offer(offer: LocalStockOffer) -> LocalStockScore:
    score = 0
    reasons: list[str] = []

    discount = offer.discount_percent
    if discount is not None:
        if discount >= 90:
            score += 75
            reasons.append("90%+ local markdown")
        elif discount >= 75:
            score += 55
            reasons.append("75%+ local markdown")
        elif discount >= 50:
            score += 35
            reasons.append("50%+ local markdown")
        elif discount >= 30:
            score += 18
            reasons.append("30%+ local markdown")

    if offer.local_price is not None:
        if offer.local_price <= 1:
            score += 50
            reasons.append("Near-zero local price")
        elif offer.best_reference_price and offer.local_price <= 10 and offer.best_reference_price >= 50:
            score += 32
            reasons.append("Very low local price compared with reference price")

    if offer.stock_quantity is not None:
        if offer.stock_quantity >= 20:
            score += 24
            reasons.append("Strong local quantity available")
        elif offer.stock_quantity >= 5:
            score += 16
            reasons.append("Useful local quantity available")
        elif offer.stock_quantity > 0:
            score += 8
            reasons.append("Limited local quantity available")
        else:
            score -= 35
            reasons.append("No local stock quantity reported")
    elif offer.stock_status:
        status = offer.stock_status.lower()
        if "in stock" in status or "available" in status:
            score += 10
            reasons.append("Store reports item in stock")
        elif "out" in status or "unavailable" in status:
            score -= 30
            reasons.append("Store reports item unavailable")

    if offer.aisle or offer.bay or offer.location_note:
        score += 12
        reasons.append("Store aisle/location proof available")

    if offer.store and offer.store.distance_miles is not None:
        if offer.store.distance_miles <= 10:
            score += 10
            reasons.append("Nearby store match")
        elif offer.store.distance_miles <= 35:
            score += 6
            reasons.append("Reasonable driving distance")

    if offer.product_url:
        score += 8
        reasons.append("Retailer product link present")

    final_score = max(0, min(150, score))
    return LocalStockScore(score=final_score, level=local_stock_level(final_score), reasons=tuple(reasons[:8]))


def local_stock_level(score: int) -> str:
    if score >= 110:
        return "hot_local"
    if score >= 80:
        return "strong_local"
    if score >= 50:
        return "watch_local"
    return "weak_local"
