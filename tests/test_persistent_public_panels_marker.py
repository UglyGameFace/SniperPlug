from pathlib import Path

PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")


def test_public_panels_are_managed_live_panels():
    assert "Live panel" in PUBLIC_ALERTS
    assert "Buttons do not expire" in PUBLIC_ALERTS
    assert "register_persistent_public_panel_views" in PUBLIC_ALERTS
