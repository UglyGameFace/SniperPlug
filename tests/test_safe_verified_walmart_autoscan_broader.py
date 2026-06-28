from pathlib import Path
import re


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_public_scout_lane_stays_private_in_autoscan():
    assert "allow_review_scout=True" not in AUTO
    assert "Review/scout leads stay private" in AUTO or "Scout Lane public post: never" in AUTO


def test_manual_autoscan_force_starts_with_deal_week():
    start = AUTO.index("def select_autoscan_preset")
    end = AUTO.index("def rotated_query_slice", start)
    body = AUTO[start:end]

    assert "if force:" in body
    assert 'HUNT_PRESETS.get("deal_week")' in body


def test_verified_autoscan_uses_broader_counts():
    fast = int(re.search(r"AUTO_SCAN_FAST_QUERY_COUNT\s*=\s*(\d+)", AUTO).group(1))
    deep = int(re.search(r"AUTO_SCAN_DEEP_QUERY_COUNT\s*=\s*(\d+)", AUTO).group(1))
    manual = int(re.search(r"AUTO_SCAN_MANUAL_QUERY_COUNT\s*=\s*(\d+)", AUTO).group(1))

    assert fast >= 8
    assert deep >= 16
    assert manual >= 32


def test_rotation_favors_deal_week_and_all_without_scout_public():
    match = re.search(r"AUTO_SCAN_CATEGORY_ROTATION\s*=\s*\((.*?)\)", AUTO, flags=re.S)
    assert match is not None
    rotation = match.group(1)

    assert '"all"' in rotation
    assert rotation.count('"deal_week"') >= 5
    assert "allow_review_scout=True" not in AUTO


def test_report_explains_setup_green_is_not_verified_finder_success():
    assert "green setup means SniperPlug can post" in AUTO
    assert "verified Walmart markdown proof" in AUTO
