from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ALERT_CONFIG = (ROOT / "sniperplug/services/public_alert_config.py").read_text(encoding="utf-8")
PUBLIC_ALERTS = (ROOT / "sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
CATEGORY_PREFS = (ROOT / "sniperplug/services/deal_category_preferences.py").read_text(encoding="utf-8")
AUTOSCAN_RUNNER = (ROOT / "sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_public_alert_config_is_guild_scoped():
    assert "guild_id INTEGER PRIMARY KEY" in PUBLIC_ALERT_CONFIG
    assert "SELECT enabled, retailers_json, channel_id FROM guild_public_alert_settings WHERE guild_id = ?" in PUBLIC_ALERT_CONFIG
    assert "ON CONFLICT(guild_id) DO UPDATE" in PUBLIC_ALERT_CONFIG
    assert "UPDATE guild_public_alert_settings SET channel_id = ?, updated_at = ? WHERE guild_id = ?" in PUBLIC_ALERT_CONFIG


def test_retailer_autoscan_settings_are_guild_and_retailer_scoped():
    assert "PRIMARY KEY (guild_id, retailer)" in PUBLIC_ALERTS
    assert "WHERE guild_id = ?" in PUBLIC_ALERTS
    assert "ON CONFLICT(guild_id, retailer)" in PUBLIC_ALERTS
    assert "SELECT retailer, enabled, interval_hours, daily_limit FROM guild_retailer_auto_scan_settings WHERE guild_id = ?" in PUBLIC_ALERTS
    assert "SELECT COUNT(*) AS count FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND day_key = ?" in PUBLIC_ALERTS
    assert "SELECT ran_at FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND scan_key = ?" in PUBLIC_ALERTS
    assert "INSERT INTO guild_retailer_auto_scan_runs (guild_id, retailer, scan_key, ran_at, day_key)" in PUBLIC_ALERTS


def test_category_preferences_are_guild_scoped():
    assert "PRIMARY KEY (guild_id, category_key)" in CATEGORY_PREFS
    assert "ON CONFLICT(guild_id, category_key)" in CATEGORY_PREFS
    assert "DELETE FROM guild_deal_category_preferences WHERE guild_id = ?" in CATEGORY_PREFS
    assert "SELECT category_key, mode FROM guild_deal_category_preferences WHERE guild_id = ?" in CATEGORY_PREFS


def test_autoscan_runtime_uses_current_guild_id_for_every_setting_path():
    assert "auto_scan_allowed(\n                self.bot.db,\n                guild.guild_id," in AUTOSCAN_RUNNER
    assert "record_auto_scan_run(self.bot.db, guild.guild_id" in AUTOSCAN_RUNNER
    assert "get_category_preferences(self.bot.db, guild.guild_id)" in AUTOSCAN_RUNNER
    assert "apply_feedback_learning_to_cards(self.bot.db, guild_id=guild.guild_id" in AUTOSCAN_RUNNER
    assert "select_fresh_deal_cards(\n            self.bot.db,\n            guild_id=guild.guild_id" in AUTOSCAN_RUNNER
    assert "maybe_post_public_deal_cards(\n            bot=self.bot,\n            guild_id=guild.guild_id" in AUTOSCAN_RUNNER
    assert "save_autoscan_report(\n            db,\n            guild_id=report.guild_id" in AUTOSCAN_RUNNER


def test_clear_cache_only_clears_current_guild():
    assert "UPDATE guild_active_deal_cache SET status = 'cleared' WHERE guild_id = ?" in PUBLIC_ALERTS
    assert "DELETE FROM guild_public_deal_posts WHERE guild_id = ?" in PUBLIC_ALERTS
    assert "DELETE FROM alert_dedupe WHERE guild_id = ? AND retailer = 'walmart'" in PUBLIC_ALERTS
