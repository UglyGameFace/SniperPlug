from pathlib import Path


BASE = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
RESILIENT = Path("sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")


def test_resilient_scheduler_is_globally_serialized_and_bounded():
    assert "AUTO_SCAN_MAX_CONCURRENCY = 1" in BASE
    assert "_WALMART_SCHEDULE_LOCK = asyncio.Lock()" in RESILIENT
    assert "for guild in guilds:" in RESILIENT
    assert "await self._run_scheduled_guild(guild)" in RESILIENT
    assert "async with _WALMART_SCHEDULE_LOCK:" in RESILIENT
    assert "query_count_override=SCHEDULED_QUERY_COUNT" in RESILIENT
    assert "SCHEDULED_QUERY_COUNT = 4" in RESILIENT


def test_scheduled_failures_are_isolated_without_killing_the_whole_pass():
    assert "except asyncio.CancelledError:" in RESILIENT
    assert "Scheduled autoscan failed but other guilds remain isolated" in RESILIENT
    assert "finally:" in RESILIENT
    assert "_NEXT_SCHEDULED_RUN_AT[gid]" in RESILIENT
    assert "asyncio.wait_for(" not in RESILIENT
    assert "AUTO_SCAN_GUILD_TIMEOUT_SECONDS" not in BASE
