from sniperplug.cogs.public_alerts import DEFAULT_AUTOSCAN_DAILY_LIMIT, DEFAULT_AUTOSCAN_INTERVAL_HOURS, default_auto_scan_config, format_auto_scan_status
from sniperplug.services.public_posting import parse_retailer_list, retailer_credit_note


def test_parse_retailer_list_normalizes_supported_aliases():
    assert parse_retailer_list("walmart, home, best buy, amz, target store") == (
        "walmart",
        "home_depot",
        "bestbuy",
        "amazon",
        "target",
    )


def test_parse_retailer_list_dedupes_and_ignores_unknowns():
    assert parse_retailer_list("walmart, walmart, target, target.com, unknown, hd") == (
        "walmart",
        "target",
        "home_depot",
    )


def test_credit_note_warns_for_limited_credit_retailers():
    assert "Limited/paid quota" in retailer_credit_note("home_depot")
    assert "Limited/paid quota" in retailer_credit_note("amazon")


def test_target_credit_note_describes_standalone_watcher():
    assert "standalone sitemap + RedSky watcher" in retailer_credit_note("target")


def test_default_auto_scan_config_is_safe_off():
    config = default_auto_scan_config("walmart")

    assert config["enabled"] is False
    assert config["interval_hours"] == DEFAULT_AUTOSCAN_INTERVAL_HOURS
    assert config["daily_limit"] == DEFAULT_AUTOSCAN_DAILY_LIMIT


def test_format_auto_scan_status_includes_interval_and_daily_limit():
    rendered = format_auto_scan_status({"walmart": {"enabled": True, "interval_hours": 4, "daily_limit": 12}})

    assert "✅ `walmart` • every 4h • max 12/day" in rendered
    assert "⛔ `home_depot`" in rendered
