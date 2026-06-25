from pathlib import Path

QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
SCOUT = Path("sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")


def test_scout_ranker_exists_for_private_watchlist():
    assert "def scout_rank(" in SCOUT
    assert "SCOUT_GRADE_FIELD" in SCOUT


def test_public_quality_keeps_scout_private():
    assert "Public Scout Lane is intentionally disabled" in QUALITY
    assert "return False" in QUALITY
