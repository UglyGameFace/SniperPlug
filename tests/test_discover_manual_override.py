from sniperplug.cogs.auto_discovery import discover_auto_scan_status, manual_discover_note


def test_manual_discover_note_allows_when_autoscan_off():
    note = manual_discover_note({"enabled": False, "interval_hours": 6, "daily_limit": 25})

    assert "manual command is allowed" in note
    assert "auto-scan is off" in note


def test_discover_auto_scan_status_says_manual_allowed():
    rendered = discover_auto_scan_status({"enabled": False, "interval_hours": 6, "daily_limit": 25})

    assert "Auto enabled: **no**" in rendered
    assert "Manual `/discover`: **allowed**" in rendered
