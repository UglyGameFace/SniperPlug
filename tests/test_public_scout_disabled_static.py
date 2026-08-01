from pathlib import Path

AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
NATIVE = Path("sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
REVIEW = Path("sniperplug/services/walmart_review_candidates.py").read_text(encoding="utf-8")
SCOUT = Path("sniperplug/services/scout_lane_polish.py").read_text(encoding="utf-8")


def test_legacy_autoscan_does_not_directly_post_scout_lane():
    assert "allow_review_scout=True" not in AUTO
    assert "Auto-scan posted public Scout Lane lead" not in AUTO


def test_native_autoscan_hides_scout_and_review_fallback_cards():
    assert "allow_review_scout=True" not in NATIVE
    assert "NATIVE_PUBLIC_SCOUT_LIMIT" not in NATIVE
    assert "Verified API Threshold + Public Scout Fallback" not in NATIVE
    assert "Exact-Verified Deals Only" in NATIVE
    assert "private review cards ready" not in NATIVE
    assert "public review posts: **0**" not in NATIVE
    assert "unverified cards shown: **0**" in NATIVE.lower()
    assert "ManualReviewShareView" not in NATIVE


def test_public_quality_has_conservative_scout_gate():
    assert "score >= 80" not in QUALITY
    assert "Walmart Cash / extra value signal detected" not in QUALITY
    assert "has_verified_api_threshold_discount" in QUALITY
    assert "PUBLIC_SCOUT_VALUE_TERMS" in QUALITY
    assert "has_low_trust_reference" in QUALITY


def test_review_cards_do_not_claim_exact_match_as_deal_proof():
    assert "shown even without Walmart markdown proof" not in REVIEW
    assert "Search route match" in REVIEW
    assert "not deal proof" in REVIEW
    assert "card.manual_share_allowed = False" in REVIEW


def test_scout_ranker_still_exists_for_explicit_non_autoscan_reviews():
    assert "def scout_rank" in SCOUT
    assert "return False" in SCOUT
