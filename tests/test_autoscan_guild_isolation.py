import asyncio
from pathlib import Path

import pytest

from sniperplug.cogs import auto_scan_runner


def test_scheduler_has_bounded_parallelism_and_timeout_guard():
    source = Path("sniperplug/cogs/auto_scan_runner.py").read_text()
    assert "AUTO_SCAN_GUILD_TIMEOUT_SECONDS = 180" in source
    assert "AUTO_SCAN_MAX_CONCURRENCY = 3" in source
    assert "asyncio.Semaphore(max(1, AUTO_SCAN_MAX_CONCURRENCY))" in source
    assert "asyncio.gather(*tasks_for_guilds, return_exceptions=True)" in source
    assert "await asyncio.wait_for(" in source
    assert "timeout=AUTO_SCAN_GUILD_TIMEOUT_SECONDS" in source
    assert "This guild was isolated so other servers could continue scanning." in source


@pytest.mark.asyncio
async def test_hung_guild_does_not_block_healthy_guild(monkeypatch):
    cog = object.__new__(auto_scan_runner.AutoScanRunnerCog)
    cog.bot = type("Bot", (), {"db": object()})()
    completed = []
    persisted = []

    async def fake_run(guild):
        if guild.guild_id == 1:
            await asyncio.sleep(0.2)
        completed.append(guild.guild_id)

    async def fake_persist(db, report, *, scan_key):
        persisted.append((report.guild_id, report.reason, scan_key))

    monkeypatch.setattr(auto_scan_runner, "AUTO_SCAN_GUILD_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(cog, "_run_guild_walmart_discovery", fake_run)
    monkeypatch.setattr(auto_scan_runner, "persist_autoscan_report", fake_persist)

    semaphore = asyncio.Semaphore(2)
    await asyncio.gather(
        cog._run_scheduled_guild(auto_scan_runner.AutoScanGuild(1, 11), semaphore),
        cog._run_scheduled_guild(auto_scan_runner.AutoScanGuild(2, 22), semaphore),
    )

    assert completed == [2]
    assert persisted and persisted[0][0] == 1
    assert "timed out" in persisted[0][1].lower()
