from pathlib import Path

QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
NATIVE = Path("sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")


def test_scout_lane_is_conservative_public_fallback():
    assert "PUBLIC_SCOUT_LANE_FIELD" in QUALITY
    assert "prepare_public_scout_candidate" in QUALITY
    assert "Public Scout Lane is enabled for high-confidence review leads" in QUALITY
    assert "PUBLIC_SCOUT_VALUE_TERMS" in QUALITY
    assert "not Verified Markdown" in QUALITY


def test_autoscan_uses_scout_fallback_when_verified_lane_empty():
    assert "NATIVE_PUBLIC_SCOUT_LIMIT = 2" in NATIVE
    assert "allow_review_scout=True" in NATIVE
    assert "Public Scout Lane posted" in NATIVE
    assert "Verified API Threshold + Public Scout Fallback" in NATIVE


def test_public_posts_have_separate_scout_dedupe_key():
    assert "PUBLIC_SCOUT_ALERT_KEY" in POSTS
    assert "allow_review_scout" in POSTS
    assert "SCOUT_ALERT_DEDUPE_HOURS" in POSTS


def test_libsql_fetchall_has_connection_safety_code():
    assert "asyncio.Lock" in DB
