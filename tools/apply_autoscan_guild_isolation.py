from __future__ import annotations

from pathlib import Path


TARGET = Path("sniperplug/cogs/auto_scan_runner.py")
TEST = Path("tests/test_autoscan_guild_isolation.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"{label} not found")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text()
    text = replace_once(
        text,
        "AUTO_SCAN_PROGRESS_SECONDS = 45\nAUTO_SCAN_DEEP_FOLLOWUP_ENABLED = True\n",
        "AUTO_SCAN_PROGRESS_SECONDS = 45\n"
        "AUTO_SCAN_DEEP_FOLLOWUP_ENABLED = True\n"
        "AUTO_SCAN_GUILD_TIMEOUT_SECONDS = 180\n"
        "AUTO_SCAN_MAX_CONCURRENCY = 3\n",
        "autoscan isolation constants",
    )

    old_loop = '''    @tasks.loop(minutes=AUTO_SCAN_INTERVAL_MINUTES)\n    async def auto_scan_loop(self) -> None:\n        await self.bot.wait_until_ready()\n        guilds = await list_public_alert_guilds(self.bot.db, bot=self.bot)\n        if not guilds:\n            return\n\n        health_error = await provider_health_error_message()\n        if health_error:\n            log.info("Auto-scan skipped: %s", health_error)\n            return\n        for guild in guilds:\n            lock = autoscan_lock(guild.guild_id)\n            if lock.locked():\n                log.info("Auto-scan skipped guild=%s because another auto-scan is already running", guild.guild_id)\n                continue\n            async with lock:\n                try:\n                    await self._run_guild_walmart_discovery(guild)\n                except Exception:\n                    log.exception("Auto-scan guild run failed but loop will continue guild=%s", guild.guild_id)\n                    continue\n\n'''
    new_loop = '''    @tasks.loop(minutes=AUTO_SCAN_INTERVAL_MINUTES)\n    async def auto_scan_loop(self) -> None:\n        await self.bot.wait_until_ready()\n        guilds = await list_public_alert_guilds(self.bot.db, bot=self.bot)\n        if not guilds:\n            return\n\n        health_error = await provider_health_error_message()\n        if health_error:\n            log.info("Auto-scan skipped: %s", health_error)\n            return\n\n        semaphore = asyncio.Semaphore(max(1, AUTO_SCAN_MAX_CONCURRENCY))\n        tasks_for_guilds = [\n            asyncio.create_task(self._run_scheduled_guild(guild, semaphore))\n            for guild in guilds\n        ]\n        results = await asyncio.gather(*tasks_for_guilds, return_exceptions=True)\n        for guild, result in zip(guilds, results):\n            if isinstance(result, asyncio.CancelledError):\n                raise result\n            if isinstance(result, Exception):\n                log.error(\n                    "Auto-scan isolated guild task escaped its guard guild=%s error=%s",\n                    guild.guild_id,\n                    clean_log_text(result),\n                )\n\n    async def _run_scheduled_guild(\n        self,\n        guild: AutoScanGuild,\n        semaphore: asyncio.Semaphore,\n    ) -> None:\n        async with semaphore:\n            lock = autoscan_lock(guild.guild_id)\n            if lock.locked():\n                log.info("Auto-scan skipped guild=%s because another auto-scan is already running", guild.guild_id)\n                return\n            async with lock:\n                try:\n                    await asyncio.wait_for(\n                        self._run_guild_walmart_discovery(guild),\n                        timeout=AUTO_SCAN_GUILD_TIMEOUT_SECONDS,\n                    )\n                except asyncio.TimeoutError:\n                    reason = (\n                        f"Auto-scan timed out after {AUTO_SCAN_GUILD_TIMEOUT_SECONDS} seconds. "\n                        "This guild was isolated so other servers could continue scanning."\n                    )\n                    report = AutoScanReport(\n                        guild_id=guild.guild_id,\n                        allowed=False,\n                        reason=reason,\n                        settings={\n                            "timeout_seconds": AUTO_SCAN_GUILD_TIMEOUT_SECONDS,\n                            "isolated": True,\n                            "retailer": AUTO_SCAN_RETAILER,\n                        },\n                    )\n                    await persist_autoscan_report(self.bot.db, report, scan_key=AUTO_SCAN_SOURCE_LABEL)\n                    log.error("Auto-scan guild timed out and was isolated guild=%s timeout_s=%s", guild.guild_id, AUTO_SCAN_GUILD_TIMEOUT_SECONDS)\n                except asyncio.CancelledError:\n                    raise\n                except Exception:\n                    log.exception("Auto-scan guild run failed but other guild tasks will continue guild=%s", guild.guild_id)\n\n'''
    text = replace_once(text, old_loop, new_loop, "scheduled autoscan loop")
    TARGET.write_text(text)

    TEST.write_text('''import asyncio\nfrom pathlib import Path\n\nimport pytest\n\nfrom sniperplug.cogs import auto_scan_runner\n\n\ndef test_scheduler_has_bounded_parallelism_and_timeout_guard():\n    source = Path("sniperplug/cogs/auto_scan_runner.py").read_text()\n    assert "AUTO_SCAN_GUILD_TIMEOUT_SECONDS = 180" in source\n    assert "AUTO_SCAN_MAX_CONCURRENCY = 3" in source\n    assert "asyncio.Semaphore(max(1, AUTO_SCAN_MAX_CONCURRENCY))" in source\n    assert "asyncio.gather(*tasks_for_guilds, return_exceptions=True)" in source\n    assert "await asyncio.wait_for(" in source\n    assert "timeout=AUTO_SCAN_GUILD_TIMEOUT_SECONDS" in source\n    assert "This guild was isolated so other servers could continue scanning." in source\n\n\n@pytest.mark.asyncio\nasync def test_hung_guild_does_not_block_healthy_guild(monkeypatch):\n    cog = object.__new__(auto_scan_runner.AutoScanRunnerCog)\n    cog.bot = type("Bot", (), {"db": object()})()\n    completed = []\n    persisted = []\n\n    async def fake_run(guild):\n        if guild.guild_id == 1:\n            await asyncio.sleep(0.2)\n        completed.append(guild.guild_id)\n\n    async def fake_persist(db, report, *, scan_key):\n        persisted.append((report.guild_id, report.reason, scan_key))\n\n    monkeypatch.setattr(auto_scan_runner, "AUTO_SCAN_GUILD_TIMEOUT_SECONDS", 0.02)\n    monkeypatch.setattr(cog, "_run_guild_walmart_discovery", fake_run)\n    monkeypatch.setattr(auto_scan_runner, "persist_autoscan_report", fake_persist)\n\n    semaphore = asyncio.Semaphore(2)\n    await asyncio.gather(\n        cog._run_scheduled_guild(auto_scan_runner.AutoScanGuild(1, 11), semaphore),\n        cog._run_scheduled_guild(auto_scan_runner.AutoScanGuild(2, 22), semaphore),\n    )\n\n    assert completed == [2]\n    assert persisted and persisted[0][0] == 1\n    assert "timed out" in persisted[0][1].lower()\n''')


if __name__ == "__main__":
    main()
