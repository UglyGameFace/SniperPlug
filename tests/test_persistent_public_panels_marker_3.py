from pathlib import Path

PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")


def test_deal_categories_custom_id_is_static():
    assert "sniperplug:panel:deal_categories:open" in PUBLIC_ALERTS
