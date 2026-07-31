from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")


def test_manual_autoscan_no_longer_has_destructive_outer_timeout():
    assert "asyncio.wait_for(" not in RUNNER
    assert "NATIVE_MANUAL_TIMEOUT_SECONDS" not in RUNNER
    assert "await self._run_guild_walmart_discovery(" in RUNNER


def test_manual_autoscan_keeps_lock_and_returns_report():
    assert "async with lock:" in RUNNER
    assert "await self._send_autoscan_report" in RUNNER
    assert "except asyncio.CancelledError:" in RUNNER


def test_manual_autoscan_sends_repeated_truthful_progress():
    assert "while True:" in RUNNER
    assert "MANUAL_PROGRESS_INTERVAL_SECONDS = 45" in RUNNER
    assert "keep completed route results and return a report instead of killing the whole pass" in RUNNER
    assert "Elapsed: **{elapsed}s**" in RUNNER


def test_bot_registers_resilient_runner_while_preserving_native_marker():
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog" in BOT
    assert "from sniperplug.cogs.resilient_auto_scan_runner import AutoScanRunnerCog as ResilientAutoScanRunnerCog" in BOT
    assert "await self.add_cog(ResilientAutoScanRunnerCog(self))" in BOT
    assert "await self.add_cog(AutoScanRunnerCog(self))" not in BOT
