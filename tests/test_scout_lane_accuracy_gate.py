import discord
from types import SimpleNamespace

from sniperplug.services.scout_lane_polish import (
    has_hard_value_signal,
    is_high_confidence_public_scout,
    select_best_public_scout_cards,
    scout_rank,
)


def make_card(title: str, *, score: int = 70, discount: float = 0, fields=()):
    embed = discord.Embed(title=title)
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    return SimpleNamespace(
        embed=embed,
        label=title,
        url="https://www.walmart.com/ip/123",
        retailer="Walmart",
        sku="123",
        upc="456",
        selected_offer_id="offer",
        current_price=12.59,
        discount=discount,
        score=score,
        manual_share_allowed=True,
    )


def test_walmart_cash_or_coupon_can_rank_private_scout_but_not_public_post():
    cash = make_card(
        "Walmart Cash eligible detergent",
        fields=[
            ("💰 API price/value", "Current product price: $9.99\nWalmart Cash: $8.00"),
            ("📦 API fields", "Stock: Available\nAvailable online: yes"),
        ],
    )
    coupon = make_card(
        "Coupon value lead",
        score=76,
        fields=[
            ("💰 API price/value", "Current product price: $14.99\nCoupon from API: $10 off"),
            ("📦 API fields", "Stock: Available\nAvailable online: yes"),
        ],
    )

    assert scout_rank(cash, min_discount=50) >= 95
    assert scout_rank(coupon, min_discount=50) >= 95
    assert has_hard_value_signal(cash, min_discount=50)
    assert has_hard_value_signal(coupon, min_discount=50)
    assert select_best_public_scout_cards([cash, coupon], min_discount=50, min_rank=95) == []


def test_trusted_discount_can_rank_private_scout_but_not_public_post():
    card = make_card(
        "Trusted markdown lead",
        discount=45,
        fields=[
            ("💰 API price/value", "Current product price: $19.99\nTrusted markdown proof found"),
            ("📦 API fields", "Stock: Available\nAvailable online: yes"),
        ],
    )

    assert scout_rank(card, min_discount=50) >= 95
    assert not is_high_confidence_public_scout(card, min_discount=50, min_rank=95)
