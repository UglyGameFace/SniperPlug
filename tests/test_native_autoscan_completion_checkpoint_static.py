from pathlib import Path


def test_native_autoscan_records_interval_only_after_persisted_completion():
    source = Path("sniperplug/cogs/native_auto_scan_runner.py").read_text()
    start = source.index("async def _run_guild_walmart_discovery")
    end = source.index("async def _send_autoscan_report", start)
    block = source[start:end]
    first_record = block.index("await legacy.record_auto_scan_run")
    first_persist = block.index("await legacy.persist_autoscan_report")
    assert first_record > first_persist
    assert block.count("await legacy.record_auto_scan_run") == 2
    assert block.count("await legacy.persist_autoscan_report") == 3
