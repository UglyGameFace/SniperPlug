from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
AUTO = (ROOT / "sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
NATIVE = (ROOT / "sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
RESILIENT = (ROOT / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")


def test_public_scout_lane_stays_private_in_autoscan():
    combined = AUTO + NATIVE + RESILIENT
    assert "allow_review_scout=True" not in combined
    assert "Anything uncertain remains private for staff review" in NATIVE


def test_manual_and_scheduled_autoscan_use_broad_public_safe_builder():
    start = NATIVE.index("def select_native_autoscan_preset")
    end = NATIVE.index("def build_native_broad_preset", start)
    selector = NATIVE[start:end]

    assert "return build_native_broad_preset(presets, guild_id=guild_id, query_count=query_count)" in selector
    assert "NATIVE_MANUAL_QUERY_COUNT if force else legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT" in selector
    assert "legacy.AUTO_SCAN_FAST_QUERY_COUNT" not in selector
    assert "if force:" not in selector
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


def test_public_safe_category_set_remains_broad_without_scout_public():
    match = re.search(r"NATIVE_CATEGORY_ROTATION\s*=\s*\((.*?)\)", NATIVE, flags=re.S)
    assert match is not None
    rotation = match.group(1)

    for category in ('"deal_week"', '"tech"', '"auto_tools"', '"home"', '"open_box"', '"beauty"', '"toys"', '"essentials"'):
        assert category in rotation
    assert "allow_review_scout=True" not in NATIVE


def test_report_explains_setup_green_is_not_verified_finder_success():
    assert "green setup means SniperPlug can post" in AUTO
    assert "verified Walmart markdown proof" in AUTO
