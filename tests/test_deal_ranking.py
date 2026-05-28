import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_ranking import rank_review_cards, rank_verified_cards


def card(title: str, *, discount: float = 0.0, current_price: float = 0.0, body: str = "") -> DealCard:
    embed = discord.Embed(title=title, description=body)
    item = DealCard(embed=embed, url=f"https://example.com/{title}", label=title, score=0, discount=discount)
    item.current_price = current_price
    return item


def test_rank_verified_prefers_higher_discount_then_lower_price():
    cards = [
        card("weak expensive", discount=50, current_price=500),
        card("strong", discount=80, current_price=300),
        card("weak cheap", discount=50, current_price=20),
    ]

    ranked = rank_verified_cards(cards)

    assert [item.label for item in ranked] == ["strong", "weak cheap", "weak expensive"]


def test_rank_review_promotes_flip_leads():
    cards = [
        card("plain review", body="Was/reference not trusted"),
        card("flip review", body="Marketplace comp and Flip estimate available"),
    ]

    ranked = rank_review_cards(cards)

    assert ranked[0].label == "flip review"
