from pathlib import Path

QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
NATIVE = Path("sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")


def test_scout_quality_helpers_remain_available_for_explicit_review_workflows():
    assert "PUBLIC_SCOUT_LANE_FIELD" in QUALITY
    assert "prepare_public_scout_candidate" in QUALITY
    assert "PUBLIC_SCOUT_VALUE_TERMS" in QUALITY
    assert "not Verified Markdown" in QUALITY


def test_autoscan_does_not_show_scout_fallback_when_verified_lane_empty():
    assert "NATIVE_PUBLIC_SCOUT_LIMIT" not in NATIVE
    assert "allow_review_scout=True" not in NATIVE
    assert "Public Scout Lane posted" not in NATIVE
    assert "Exact-Verified Deals Only" in NATIVE
    assert "Anything uncertain is suppressed and never shown as a deal" in NATIVE
    assert "ManualReviewShareView" not in NATIVE


def test_public_posts_retain_explicit_scout_guard_for_non_autoscan_callers():
    assert "PUBLIC_SCOUT_ALERT_KEY" in POSTS
    assert "allow_review_scout" in POSTS
    assert "SCOUT_ALERT_DEDUPE_HOURS" in POSTS


def test_libsql_fetchall_has_connection_safety_code():
    assert "asyncio.Lock" in DB
