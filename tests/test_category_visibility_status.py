from pathlib import Path

from sniperplug.services.deal_category_preferences import summarize_category_preferences


PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")


def test_category_summary_shows_empty_state():
    text = summarize_category_preferences({})
    assert "No category preferences saved yet" in text
    assert "/deal_categories" in text


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
    assert "Current category settings" in PUBLIC_ALERTS
    assert 'name="Category preferences"' in PUBLIC_ALERTS
    assert "summarize_category_preferences" in PUBLIC_ALERTS
    assert "/deal_categories" in PUBLIC_ALERTS
