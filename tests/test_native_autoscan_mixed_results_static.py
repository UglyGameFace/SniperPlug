from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "sniperplug" / "cogs" / "native_auto_scan_runner.py"
BOT = ROOT / "sniperplug" / "bot.py"


def test_runtime_uses_single_native_autoscan_runner() -> None:
    source = BOT.read_text(encoding="utf-8")
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog" in source
    assert "native_auto_scan_runner_v2" not in source


def test_review_cards_are_collected_even_when_verified_cards_exist() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "prepare_review_watchlist_cards(result, limit=NATIVE_REVIEW_CARD_LIMIT)" in source
    assert "if not shown_cards else []" not in source


def test_successful_public_post_does_not_hide_private_review_cards() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "if not report.allowed:" in source
    assert "if not report.allowed or report.public_result.posted:" not in source
