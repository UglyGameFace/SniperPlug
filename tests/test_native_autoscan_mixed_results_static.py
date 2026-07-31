from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "sniperplug" / "cogs" / "native_auto_scan_runner.py"
RESILIENT = ROOT / "sniperplug" / "cogs" / "resilient_auto_scan_runner.py"
BOT = ROOT / "sniperplug" / "bot.py"


def test_resilient_runtime_inherits_the_single_native_implementation() -> None:
    bot_source = BOT.read_text(encoding="utf-8")
    resilient_source = RESILIENT.read_text(encoding="utf-8")
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\n" not in bot_source
    assert "from sniperplug.cogs.resilient_auto_scan_runner import AutoScanRunnerCog as ResilientAutoScanRunnerCog" in bot_source
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog as NativeAutoScanRunnerCog" in resilient_source
    assert "class AutoScanRunnerCog(NativeAutoScanRunnerCog):" in resilient_source
    assert "native_auto_scan_runner_v2" not in bot_source + resilient_source


def test_review_cards_are_collected_even_when_verified_cards_exist() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "prepare_review_watchlist_cards(result, limit=NATIVE_REVIEW_CARD_LIMIT)" in source
    assert "if not shown_cards else []" not in source


def test_successful_public_post_does_not_hide_private_review_cards() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "if not report.allowed:" in source
    assert "if not report.allowed or report.public_result.posted:" not in source
