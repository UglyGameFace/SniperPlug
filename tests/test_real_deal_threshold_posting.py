import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.public_deal_quality import (
    is_public_deal_candidate,
    prepare_public_deal_candidate,
    select_public_deal_candidates,
)


def test_real_50_percent_deal_becomes_public_alertable():
    card = DealCard(
        embed=discord.Embed(title="Real Walmart deal"),
        url="https://www.walmart.com/ip/123",
        label="Real Walmart deal",
        score=62,
        discount=50,
    )
    card.current_price = 19.99

    assert prepare_public_deal_candidate(card, source_label="autoscan:walmart_discovery:deal_week", min_discount=50)
    assert card.should_alert is True
    assert card.score >= 90


def test_zero_percent_junk_is_not_public_candidate():
    card = DealCard(
        embed=discord.Embed(title="Random mobile phone"),
        url="https://www.walmart.com/ip/456",
        label="Random mobile phone",
        score=115,
        discount=0,
    )
    card.current_price = 4.99

    assert not is_public_deal_candidate(card, source_label="autoscan:walmart_discovery:watchlist", min_discount=50)


def test_walmart_cash_can_be_public_candidate_without_markdown():
    embed = discord.Embed(title="Walmart Cash eligible personal care")
    embed.add_field(name="Walmart Cash Offers", value="Walmart Cash eligible", inline=False)
    card = DealCard(
        embed=embed,
        url="https://www.walmart.com/ip/789",
        label="Walmart Cash eligible personal care",
        score=90,
        discount=0,
    )
    card.current_price = 12.99

    assert is_public_deal_candidate(card, source_label="deals", min_discount=50)


def test_select_public_deal_candidates_filters_junk_but_keeps_real_deal():
    junk = DealCard(embed=discord.Embed(title="Junk"), url="u", label="Junk", score=120, discount=0)
    junk.current_price = 1.99
    real = DealCard(embed=discord.Embed(title="Real"), url="u2", label="Real", score=50, discount=55)
    real.current_price = 24.99

    selected = select_public_deal_candidates([junk, real], source_label="deals", min_discount=50)

    assert selected == [real]
    assert real.should_alert is True
