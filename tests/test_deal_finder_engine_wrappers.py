import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_finder_engine import rank_review_candidate_result
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult


def make_review_card(label: str, body: str) -> DealCard:
    embed = discord.Embed(title=label, description=body)
    return DealCard(embed=embed, url=f"https://example.com/{label}", label=label)


def test_rank_review_candidate_result_preserves_counts_and_promotes_flip_cards():
    plain = make_review_card("plain", "Was/reference not trusted")
    flip = make_review_card("flip", "Marketplace comp and Flip estimate available")
    result = ReviewCandidateResult(
        cards=[plain, flip],
        under_threshold_count=1,
        missing_reference_count=2,
        weak_reference_count=3,
        missing_current_count=4,
        no_value_signal_count=5,
        rejected_bad_value_count=6,
    )

    ranked = rank_review_candidate_result(result)

    assert ranked.cards[0].label == "flip"
    assert ranked.under_threshold_count == 1
    assert ranked.missing_reference_count == 2
    assert ranked.weak_reference_count == 3
    assert ranked.missing_current_count == 4
    assert ranked.no_value_signal_count == 5
    assert ranked.rejected_bad_value_count == 6
