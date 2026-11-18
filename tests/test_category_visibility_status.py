from pathlib import Path

from sniperplug.services.deal_category_preferences import summarize_category_preferences


PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
CANONICAL = Path("sniperplug/cogs/canonical_public_alerts.py").read_text(encoding="utf-8")


def test_category_summary_shows_empty_state():
    text = summarize_category_preferences({})
    assert "No category preferences saved yet" in text
    assert "/walmart categories" in text


def test_category_summary_shows_priority_and_muted():
    text = summarize_category_preferences({
        "mobile_accessories": "priority",
        "pet_supplies": "muted",
    })
    assert "Priority" in text
    assert "Mobile Accessories" in text
    assert "Muted" in text
    assert "Pet Supplies" in text
    assert "Extreme/nuclear" in text


def test_status_and_health_show_category_preferences():
    # The category dashboard view (live via /walmart categories and the
    # persistent panels) shows the current settings summary.
    assert "Current category settings" in PUBLIC_ALERTS
    assert "summarize_category_preferences" in PUBLIC_ALERTS
    assert "/walmart categories" in PUBLIC_ALERTS
    # The canonical health path reads the preferences for delivery decisions.
    assert "get_category_preferences" in CANONICAL
    # The 'name="Category preferences"' field belonged to PublicAlertsCog's
    # status command, removed in the 2026-11-18 dead-feature cleanup (the
    # cog was never loaded, so that status view never ran in prod).
    assert 'name="Category preferences"' not in PUBLIC_ALERTS
