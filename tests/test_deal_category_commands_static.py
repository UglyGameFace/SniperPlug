from pathlib import Path


PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
AUTOSCAN = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_category_commands_exist():
    assert 'name="deal_categories"' in PUBLIC_ALERTS
    assert 'name="deal_category"' not in PUBLIC_ALERTS
    assert "priority|normal|muted" in PUBLIC_ALERTS


def test_autoscan_applies_category_preferences_before_public_posting():
    assert "get_category_preferences" in AUTOSCAN
    assert "apply_category_preferences" in AUTOSCAN

    apply_index = AUTOSCAN.index("apply_category_preferences")
    post_index = AUTOSCAN.index("maybe_post_public_deal_cards", apply_index)
    assert apply_index < post_index


def test_health_mentions_category_preference_gate():
    assert "category preference" in PUBLIC_ALERTS
    assert "extreme/nuclear markdowns still override" in PUBLIC_ALERTS
