import discord

from sniperplug.cogs.auto_scan_runner import (
    AUTO_SCAN_CATEGORY_ROTATION,
    prepare_review_watchlist_cards,
    watchlist_repeat_summary,
)
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS, VerifiedHuntResult
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult


def test_deal_week_preset_exists_and_targets_requested_categories():
    preset = HUNT_PRESETS["deal_week"]
    joined = " | ".join(preset.queries).lower()

    assert "walmart deal week" in joined
    assert "gaming monitor" in joined
    assert "gaming headset" in joined
    assert "motor oil" in joined
    assert "dolce gabbana" in joined
    assert "gold chain" in joined
    assert AUTO_SCAN_CATEGORY_ROTATION.count("deal_week") >= 3


def test_prepare_review_watchlist_cards_marks_leads_without_hiding_warning():
    card = DealCard(
        embed=discord.Embed(title="Dell Monitor Review Lead"),
        url="https://www.walmart.com/ip/123",
        label="Dell Monitor",
        score=40,
        discount=35,
    )
    review = ReviewCandidateResult(cards=[card])
    result = VerifiedHuntResult(
        cards=[],
        pages_checked=1,
        products_checked=1,
        warnings=[],
        searches_attempted=1,
        min_discount=70,
        review_candidates=review,
    )

    cards = prepare_review_watchlist_cards(result, limit=1)

    assert len(cards) == 1
    assert getattr(cards[0], "should_alert") is True
    assert cards[0].score >= 90
    assert str(cards[0].embed.title).startswith("🟨 Watchlist")
    assert any(field.name == "🟨 Walmart Deal Week Watchlist" for field in cards[0].embed.fields)


def test_watchlist_repeat_summary_mentions_fallback():
    card = DealCard(embed=discord.Embed(title="Lead"), url="u", label="Lead")
    assert "watchlist fallback posted **1**" in watchlist_repeat_summary("fresh: none", [card])
