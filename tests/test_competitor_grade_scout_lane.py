from pathlib import Path


POLISH = Path("sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")


def test_scout_lane_has_ranker_and_buy_check():
    assert "def scout_rank" in POLISH
    assert "def select_best_public_scout_cards" in POLISH
    assert "SniperPlug Scout Grade" in POLISH
    assert "20-second buy check" in POLISH
    assert "Verify before buying" in POLISH


def test_autoscan_uses_ranked_scout_cards_not_raw_review_slice():
    assert "select_best_public_scout_cards" in AUTO
    assert "scout_source_cards" in AUTO
    assert "review.cards[: max" not in AUTO
    assert "high-confidence leads" in AUTO


def test_public_quality_uses_scout_rank_and_lower_scout_threshold():
    assert "scout_rank(card)" in QUALITY
    assert "min_score: int = 95" in QUALITY
    assert "prepare_public_scout_candidate" in QUALITY


def test_old_private_watchlist_expectation_removed():
    assert "were kept private in diagnostics" not in AUTO
    assert "allow_review_scout=True" in AUTO


def test_scout_lane_never_claims_verified_certainty():
    assert "Lane: **High-confidence Scout**, not Verified" in POLISH
    assert "not verified proof" in POLISH or "not blind-buy proof" in POLISH
    assert "Verify before buying" in POLISH
