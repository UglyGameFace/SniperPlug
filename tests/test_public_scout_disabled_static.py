from pathlib import Path

AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
REVIEW = Path("sniperplug/services/walmart_review_candidates.py").read_text(encoding="utf-8")
SCOUT = Path("sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")


def test_autoscan_does_not_public_post_scout_lane():
    assert "allow_review_scout=True" not in AUTO
    assert "Public Scout Lane is disabled for public posts" in AUTO
    assert "Auto-scan posted public Scout Lane lead" not in AUTO


def test_public_quality_has_no_score_or_cash_bypass():
    assert "score >= 80" not in QUALITY
    assert "Walmart Cash / extra value signal detected" not in QUALITY
    assert "has_verified_api_threshold_discount" in QUALITY
    assert "Public Scout Lane is intentionally disabled" in QUALITY


def test_review_cards_do_not_claim_exact_match_as_deal_proof():
    assert "shown even without Walmart markdown proof" not in REVIEW
    assert "Search route match" in REVIEW
    assert "not deal proof" in REVIEW
    assert "card.manual_share_allowed = False" in REVIEW


def test_scout_backup_gate_is_false():
    assert "Public Scout Lane is disabled" in SCOUT
    assert "return False" in SCOUT
