from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = (ROOT / "sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")
INTELLIGENCE = (ROOT / "sniperplug/services/autoscan_no_post_intelligence.py").read_text(encoding="utf-8")
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")


def test_no_post_intelligence_is_kept_out_of_observed_memory_rewriter():
    assert "install_autoscan_observed_price_memory" not in OBSERVED
    assert "auto_scan_runner.autoscan_blocker_summary" not in OBSERVED
    assert "native_auto_scan_runner" in BOT


def test_no_post_intelligence_reports_memory_routes_and_blockers():
    assert "Scan volume" in INTELLIGENCE
    assert "Verified/public funnel" in INTELLIGENCE
    assert "Observed price memory" in INTELLIGENCE
    assert "Proof blockers" in INTELLIGENCE
    assert "Private review/scout leads" in INTELLIGENCE
    assert "Top routes checked" in INTELLIGENCE
    assert "Final public guard blocks" in INTELLIGENCE
