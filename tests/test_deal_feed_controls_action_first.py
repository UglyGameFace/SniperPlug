from pathlib import Path


PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")


def test_deal_feed_controls_have_best_setup_button():
    assert "class DealCategoryBestSetupButton" in PUBLIC_ALERTS
    assert "Best Setup" in PUBLIC_ALERTS
    assert 'apply_preset(self.dashboard.db, self.dashboard.guild_id, "deal_week")' in PUBLIC_ALERTS
    assert 'apply_preset(self.dashboard.db, self.dashboard.guild_id, "walmart_cash")' in PUBLIC_ALERTS


def test_dashboard_is_action_first_not_wall_of_text_first():
    assert "**Start here:** tap **✅ Best Setup**" in PUBLIC_ALERTS
    assert "What to tap" in PUBLIC_ALERTS
    assert "Manual editing" in PUBLIC_ALERTS
    assert "Selected → ON" in PUBLIC_ALERTS
    assert "Selected → Mute" in PUBLIC_ALERTS


def test_no_selected_category_message_points_to_best_setup():
    assert "For quick setup, tap **✅ Best Setup**" in PUBLIC_ALERTS
