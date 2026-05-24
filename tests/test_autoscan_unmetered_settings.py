from sniperplug.cogs.public_alerts import format_auto_scan_status, format_daily_limit, format_interval


def test_format_interval_supports_no_interval_gate():
    assert format_interval(0) == "no interval gate"
    assert format_interval(2) == "every 2h"


def test_format_daily_limit_supports_no_daily_gate():
    assert format_daily_limit(0) == "no daily gate"
    assert format_daily_limit(25) == "max 25/day"


def test_format_auto_scan_status_shows_walmart_unmetered_mode():
    rendered = format_auto_scan_status({"walmart": {"enabled": True, "interval_hours": 0, "daily_limit": 0}})

    assert "✅ `walmart` • no interval gate • no daily gate" in rendered
