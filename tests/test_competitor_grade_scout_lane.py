from pathlib import Path

QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
SCOUT = Path("sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")


def test_scout_ranker_exists_for_review_watchlist():
    assert "def scout_rank(" in SCOUT
    assert "SCOUT_GRADE_FIELD" in SCOUT


def test_public_quality_uses_conservative_scout_gate():
    assert "def prepare_public_scout_candidate" in QUALITY
    assert "PUBLIC_SCOUT_VALUE_TERMS" in QUALITY
    assert "has_low_trust_reference" in QUALITY
    assert "not Verified Markdown" in QUALITY
