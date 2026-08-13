from __future__ import annotations

from dataclasses import dataclass

from sniperplug.cogs.deal_scanner import DealCard


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
    """Rank private review cards, including exact product matches and flip-comp leads."""
    return sorted(cards, key=review_card_score, reverse=True)


def verified_card_score(card: DealCard) -> tuple[float, float, float, float]:
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    score = float(getattr(card, "score", 0.0) or 0.0)
    current_price = float(getattr(card, "current_price", 0.0) or 0.0)
    # Prefer higher discounts and scoring, then lower entry price for easier user action.
    return (discount, score, -current_price, 1.0 if getattr(card, "should_alert", True) else 0.0)


def review_card_score(card: DealCard) -> tuple[float, float, float]:
    text = str(card.embed.to_dict()).lower()
    flip_bonus = 35.0 if "flip estimate" in text or "marketplace comp" in text else 0.0
    value_bonus = 20.0 if "coupon from api" in text or "walmart cash from api" in text else 0.0
    direct_match_score = float(getattr(card, "product_match_score", 0.0) or 0.0)
    direct_match_bonus = 120.0 * max(0.0, min(1.0, direct_match_score)) if getattr(card, "product_search_match", False) else 0.0
    current_price = float(getattr(card, "current_price", 0.0) or 0.0)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    return (direct_match_bonus + flip_bonus + value_bonus + discount, -current_price, float(getattr(card, "score", 0.0) or 0.0))
