from types import SimpleNamespace

import discord

from sniperplug.services.scout_lane_polish import (
    has_hard_value_signal,
    is_high_confidence_public_scout,
    scout_rank,
    select_best_public_scout_cards,
)


def make_card(*, title: str, price: float = 12.59, discount: float = 0, score: int = 0, fields: list[tuple[str, str]] | None = None):
    embed = discord.Embed(title=title)
    for name, value in fields or []:
        embed.add_field(name=name, value=value, inline=False)
    return SimpleNamespace(
        embed=embed,
        label=title,
        url="https://www.walmart.com/ip/test",
        retailer="walmart",
        sku="123",
        upc="456",
        selected_offer_id="offer",
        current_price=price,
        discount=discount,
        score=score,
        manual_share_allowed=True,
    )


def test_low_trust_msrp_board_game_does_not_public_post():
    card = make_card(
        title="Triangle Takeover by Relatable",
        score=0,
        fields=[
            ("💰 API price/value", "Current product price: $12.59\nIgnored reference: $16.91 msrp\nReference match: blocked as low-trust/suspicious"),
            ("📦 API fields", "Finder query: board game clearance\nStock: Available\nAvailable online: yes"),
        ],
    )

    assert not has_hard_value_signal(card, min_discount=50)
    assert scout_rank(card, min_discount=50) < 95
    assert not is_high_confidence_public_scout(card, min_discount=50, min_rank=95)
    assert select_best_public_scout_cards([card], min_discount=50, min_rank=95) == []


def test_manual_share_allowed_does_not_override_weak_proof():
    card = make_card(
        title="Weak manual scout",
        score=150,
        fields=[
            ("💰 API price/value", "Ignored reference: $20 msrp\nReference match: blocked as low-trust/suspicious"),
        ],
    )
    card.manual_share_allowed = True

    assert not is_high_confidence_public_scout(card, min_discount=50, min_rank=95)


def test_walmart_cash_or_coupon_can_public_scout():
    cash = make_card(
        title="Walmart Cash eligible detergent",
        score=70,
        fields=[
            ("💰 API price/value", "Current product price: $9.99\nWalmart Cash: $8.00"),
            ("📦 API fields", "Stock: Available\nAvailable online: yes"),
        ],
    )
    coupon = make_card(
        title="Coupon value lead",
        score=76,
        fields=[
            ("💰 API price/value", "Current product price: $14.99\nCoupon from API: $10 off"),
            ("📦 API fields", "Stock: Available\nAvailable online: yes"),
        ],
    )

    selected = select_best_public_scout_cards([cash, coupon], min_discount=50, min_rank=95)
    assert len(selected) >= 1
    assert all(is_high_confidence_public_scout(card, min_discount=50, min_rank=95) for card in selected)


def test_trusted_discount_can_public_scout():
    card = make_card(
        title="Trusted markdown lead",
        discount=45,
        score=70,
        fields=[
            ("💰 API price/value", "Current product price: $19.99\nTrusted markdown proof found"),
            ("📦 API fields", "Stock: Available\nAvailable online: yes"),
        ],
    )

    assert is_high_confidence_public_scout(card, min_discount=50, min_rank=95)
