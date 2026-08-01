from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
AUTO = (ROOT / "sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
RESILIENT = (ROOT / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")
COVERAGE = (ROOT / "sniperplug/services/walmart_catalog_coverage.py").read_text(encoding="utf-8")


def test_public_scout_lane_is_never_user_visible_in_autoscan():
    combined = AUTO + NATIVE + RESILIENT
    assert "allow_review_scout=True" not in combined
    assert "Anything uncertain is suppressed and never shown as a deal" in NATIVE
    assert "ManualReviewShareView" not in NATIVE


def test_manual_and_scheduled_autoscan_use_catalog_rotation_builder():
    start = NATIVE.index("def select_native_autoscan_preset")
    end = NATIVE.index("def build_native_broad_preset", start)
    selector = NATIVE[start:end]

    assert "rotating_catalog_slice(guild_id=guild_id, query_count=query_count)" in selector
    assert "build_native_broad_preset" in selector
    assert "NATIVE_MANUAL_QUERY_COUNT if force else legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT" in NATIVE
    assert "legacy.AUTO_SCAN_FAST_QUERY_COUNT" not in selector
    assert "query_count_override=8" in RESILIENT
    assert "query_count_override=SCHEDULED_QUERY_COUNT" in RESILIENT


def test_verified_autoscan_uses_bounded_scheduled_and_manual_counts():
    scheduled = int(re.search(r"AUTO_SCAN_SCHEDULED_QUERY_COUNT\s*=\s*(\d+)", AUTO).group(1))
    manual = int(re.search(r"AUTO_SCAN_MANUAL_QUERY_COUNT\s*=\s*(\d+)", AUTO).group(1))
    resilient_scheduled = int(re.search(r"SCHEDULED_QUERY_COUNT\s*=\s*(\d+)", RESILIENT).group(1))

    assert scheduled == 4
    assert resilient_scheduled == scheduled
    assert manual == 8
    assert "AUTO_SCAN_FAST_QUERY_COUNT" not in AUTO
    assert "AUTO_SCAN_DEEP_QUERY_COUNT" not in AUTO


def test_catalog_pool_is_broad_and_includes_cash_without_scout_public():
    for route in (
        '"walmart electronics"',
        '"walmart grocery"',
        '"walmart home"',
        '"walmart tools"',
        '"walmart beauty"',
        '"walmart clothing"',
    ):
        assert route in COVERAGE
    assert "CATEGORY_ROUTES" in COVERAGE
    assert "DEFAULT_CASH_QUERIES" in COVERAGE
    assert "allow_review_scout=True" not in NATIVE


def test_catalog_rotation_keeps_each_pass_bounded_and_persistent():
    assert "ROTATION_SECONDS = 15 * 60" in COVERAGE
    assert "slot_count = max(1, math.ceil(len(pool) / count))" in COVERAGE
    assert "selected = list(pool[start : start + count])" in COVERAGE
    assert "Every discovered Walmart item ID is retained in the global exact-detail queue" in COVERAGE


def test_report_explains_setup_green_is_not_verified_finder_success():
    assert "green setup means SniperPlug can post" in AUTO
    assert "verified Walmart markdown proof" in AUTO
