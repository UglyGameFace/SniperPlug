from pathlib import Path

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.autoscan_decision_trail import explain_autoscan_decision_trail, no_post_plain_english


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_decision_trail_explains_blocked_cards():
    low_conf = DealCard(embed=discord.Embed(title="Low confidence deal"), url="u1", label="Low confidence deal", score=40, discount=55)
    low_conf.current_price = 19.99
    fresh = DealCard(embed=discord.Embed(title="Fresh deal"), url="u2", label="Fresh deal", score=95, discount=60)
    fresh.current_price = 9.99

    text = explain_autoscan_decision_trail(
        all_verified_cards=[low_conf, fresh],
        confidence_cards=[fresh],
        public_candidates=[fresh],
        fresh_cards=[fresh],
        min_discount=50,
        confidence_floor=78,
        limit=8,
    )

    assert "Low confidence deal" in text
    assert "below confidence floor" in text
    assert "Fresh deal" in text
    assert "sent to public guard" in text


def test_no_post_plain_english_has_distinct_reasons():
    assert "No verified markdown" in no_post_plain_english(verified_count=0, public_candidate_count=0, fresh_count=0, posted_count=0)
    assert "final public-quality lane" in no_post_plain_english(verified_count=2, public_candidate_count=0, fresh_count=0, posted_count=0)
    assert "fresh/duplicate/preflight" in no_post_plain_english(verified_count=2, public_candidate_count=2, fresh_count=0, posted_count=0)
    assert "Posted" in no_post_plain_english(verified_count=2, public_candidate_count=2, fresh_count=2, posted_count=1)


def test_autoscan_report_contains_candidate_decision_trail():
    assert "decision_trail_summary" in AUTO
    assert "Candidate decision trail" in AUTO
    assert "explain_autoscan_decision_trail" in AUTO
    assert "public-quality cards that passed final posting gates" in AUTO
