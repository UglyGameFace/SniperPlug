from __future__ import annotations

from dataclasses import dataclass

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.product_demand_evidence import assess_product_demand


DIRECT_MATCH_VISIBLE_LIMIT = 5


@dataclass(frozen=True)
class RankedCards:
    verified: list[DealCard]
    review: list[DealCard]

    @property
    def all_cards(self) -> list[DealCard]:
        return [*self.verified, *self.review]


def rank_verified_cards(cards: list[DealCard]) -> list[DealCard]:
    """Rank public-alertable verified cards by proof strength and useful value."""
    return sorted(cards, key=verified_card_score, reverse=True)


def rank_review_cards(cards: list[DealCard]) -> list[DealCard]:
    """Rank private review cards, keeping strong direct search matches visible."""
    ranked = sorted(cards, key=review_card_score, reverse=True)
    if any(getattr(card, "product_search_match", False) for card in ranked):
        return ranked[:DIRECT_MATCH_VISIBLE_LIMIT]
    return ranked


def verified_card_score(card: DealCard) -> tuple[float, float, float, float]:
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    score = float(getattr(card, "score", 0.0) or 0.0)
    current_price = float(getattr(card, "current_price", 0.0) or 0.0)
    # Markdown stays first; trustworthy item engagement breaks comparable ties.
    interest = assess_product_demand(card).rank_points
    return (discount, score + interest, -current_price, 1.0 if getattr(card, "should_alert", True) else 0.0)


def review_card_score(card: DealCard) -> tuple[float, float, float]:
    text = str(card.embed.to_dict()).lower()
    # Text claims like "flip estimate" or "marketplace comp" are not actual
    # sale receipts. They must never push a speculative lead to the top.
    interest_bonus = float(assess_product_demand(card).rank_points)
    value_bonus = 20.0 if "coupon from api" in text or "walmart cash from api" in text else 0.0
    direct_match_score = float(getattr(card, "product_match_score", 0.0) or 0.0)
    direct_match_bonus = 120.0 * max(0.0, min(1.0, direct_match_score)) if getattr(card, "product_search_match", False) else 0.0
    current_price = float(getattr(card, "current_price", 0.0) or 0.0)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    return (direct_match_bonus + interest_bonus + value_bonus + discount, -current_price, float(getattr(card, "score", 0.0) or 0.0))
