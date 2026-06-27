import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult


def test_review_summary_names_strongest_private_leads():
    card = DealCard(
        embed=discord.Embed(title="🟨 Review candidate • Hyper Tough Tire Inflator"),
        url="https://www.walmart.com/ip/123",
        label="Hyper Tough Tire Inflator Portable Air Compressor",
        score=0,
        discount=0,
    )
    card.current_price = 19.88

    summary = ReviewCandidateResult(
        cards=[card],
        weak_reference_count=422,
        missing_reference_count=25,
        exact_match_count=419,
    ).summary_line()

    assert "review candidates: **1**" in summary
    assert "strongest private leads kept" in summary
    assert "Hyper Tough Tire Inflator" in summary
    assert "$19.88" in summary
    assert "flip/review" in summary
