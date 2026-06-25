import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.public_deal_quality import (
    is_public_deal_candidate,
    prepare_public_deal_candidate,
    select_public_deal_candidates,
)


def test_real_50_percent_deal_becomes_public_alertable_without_fake_score_inflation():
    card = DealCard(
        embed=discord.Embed(title="Real Walmart deal"),
        url="https://www.walmart.com/ip/123",
        label="Real Walmart deal",
        score=62,
        discount=50,
    )
    card.current_price = 19.99

    assert prepare_public_deal_candidate(
        card,
        source_label="autoscan:walmart_discovery:deal_week",
        min_discount=50,
    )
    assert card.should_alert is True

    # Do not make up confidence/score numbers. Public posting is allowed
    # because the API markdown meets threshold, not because we inflated score.
    assert card.score == 62


def test_49_percent_deal_does_not_post_when_threshold_is_50():
    card = DealCard(
        embed=discord.Embed(title="Almost deal"),
        url="https://www.walmart.com/ip/124",
        label="Almost deal",
        score=150,
        discount=49,
    )
    card.current_price = 19.99

    assert not is_public_deal_candidate(
        card,
        source_label="autoscan:walmart_discovery:deal_week",
        min_discount=50,
    )


def test_zero_percent_junk_is_not_public_candidate():
    card = DealCard(
        embed=discord.Embed(title="Random mobile phone"),
        url="https://www.walmart.com/ip/456",
        label="Random mobile phone",
        score=115,
        discount=0,
    )
    card.current_price = 4.99

    assert not is_public_deal_candidate(
        card,
        source_label="autoscan:walmart_discovery:watchlist",
        min_discount=50,
    )


def test_walmart_cash_cannot_be_public_candidate_without_verified_markdown():
    embed = discord.Embed(title="Walmart Cash eligible personal care")
    embed.add_field(name="Walmart Cash Offers", value="Walmart Cash eligible", inline=False)

    card = DealCard(
        embed=embed,
        url="https://www.walmart.com/ip/789",
        label="Walmart Cash eligible personal care",
        score=150,
        discount=0,
    )
    card.current_price = 12.99

    # Walmart Cash is useful to show/track, but it is not a 50% verified markdown.
    assert not is_public_deal_candidate(card, source_label="deals", min_discount=50)


def test_low_trust_msrp_reference_blocks_even_if_discount_number_is_high():
    card = DealCard(
        embed=discord.Embed(title="MSRP fake deal"),
        url="https://www.walmart.com/ip/999",
        label="Ignored reference: $189.99 MSRP low-trust/suspicious",
        score=150,
        discount=89,
    )
    card.current_price = 19.99

    assert not is_public_deal_candidate(card, source_label="deals", min_discount=50)


def test_select_public_deal_candidates_filters_junk_but_keeps_real_api_threshold_deal():
    junk = DealCard(
        embed=discord.Embed(title="Junk"),
        url="u",
        label="Junk",
        score=120,
        discount=0,
    )
    junk.current_price = 1.99

    walmart_cash = DealCard(
        embed=discord.Embed(title="Walmart Cash but no markdown"),
        url="u-cash",
        label="Walmart Cash eligible",
        score=150,
        discount=0,
    )
    walmart_cash.current_price = 12.99

    real = DealCard(
        embed=discord.Embed(title="Real"),
        url="https://www.walmart.com/ip/real",
        label="Real",
        score=50,
        discount=55,
        deal_lane="verified_markdown",
        api_current_price=24.99,
        api_reference_price=55.53,
        api_discount_percent=55,
        api_price_path="salePrice",
        api_reference_path="wasPrice",
        direct_product_url="https://www.walmart.com/ip/real",
    )
    real.current_price = 24.99

    selected = select_public_deal_candidates(
        [junk, walmart_cash, real],
        source_label="deals",
        min_discount=50,
    )

    assert selected == [real]
    assert real.should_alert is True
    assert real.score == 50
