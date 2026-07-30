from pathlib import Path


def test_autoscan_guild_discovery_isolates_bad_rows_and_configs():
    source = Path("sniperplug/cogs/auto_scan_runner.py").read_text()
    start = source.index("async def list_public_alert_guilds")
    end = source.index("def autoscan_blocker_summary", start)
    block = source[start:end]
    assert "Auto-scan skipped malformed public-alert guild id" in block
    assert "public-alert config could not be read" in block
    assert "malformed public-alert channel id" in block
    assert "continue" in block
