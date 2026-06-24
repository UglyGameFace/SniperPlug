from pathlib import Path

REVIEW = Path("sniperplug/services/walmart_review_candidates.py").read_text(encoding="utf-8")
PROVIDER = Path("sniperplug/providers/walmart.py").read_text(encoding="utf-8")
ALERT = Path("sniperplug/services/alert_renderer.py").read_text(encoding="utf-8")
SCOUT = Path("sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")


def test_provider_uses_api_value_proof():
    assert "extract_walmart_api_value_proof" in PROVIDER
    assert "api_value_proof" in PROVIDER
    assert "Walmart API promo detected" in PROVIDER


def test_review_cards_show_api_savings_and_promo():
    assert "Walmart API savings" in REVIEW
    assert "Walmart API promo cap" in REVIEW
    assert "Walmart API promo:" in REVIEW
    assert "api_value_signal" in REVIEW


def test_public_embeds_have_api_value_proof_field():
    assert "Walmart API value proof" in ALERT
    assert "def walmart_api_value_lines" in ALERT


def test_scout_lane_accepts_api_promo_as_hard_value():
    assert "walmart api savings" in SCOUT
    assert "walmart api promo" in SCOUT
    assert "buy more" in SCOUT
