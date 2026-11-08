from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = (ROOT / "sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
RESILIENT = (ROOT / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")
GLOBAL = (ROOT / "sniperplug/cogs/global_auto_scan_runner.py").read_text(encoding="utf-8")


def test_no_post_intelligence_is_kept_out_of_observed_memory_rewriter():
    assert "install_autoscan_observed_price_memory" not in OBSERVED
    assert "auto_scan_runner.autoscan_blocker_summary" not in OBSERVED
    assert "from sniperplug.cogs.global_auto_scan_runner import AutoScanRunnerCog as GlobalAutoScanRunnerCog" in BOT
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\n" not in BOT
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog as NativeAutoScanRunnerCog" in RESILIENT
    assert "class AutoScanRunnerCog(resilient.AutoScanRunnerCog)" in GLOBAL

# NOTE (C6): test_no_post_intelligence_reports_memory_routes_and_blockers was
# deleted with sniperplug/services/autoscan_no_post_intelligence.py (zero
# runtime importers). The no-post explanation now lives in the live
# autoscan_decision_trail module (see test_autoscan_decision_trail.py).
