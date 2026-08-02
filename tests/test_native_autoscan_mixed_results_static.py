from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "sniperplug" / "cogs" / "native_auto_scan_runner.py"
RESILIENT = ROOT / "sniperplug" / "cogs" / "resilient_auto_scan_runner.py"
GLOBAL = ROOT / "sniperplug" / "cogs" / "global_auto_scan_runner.py"
BOT = ROOT / "sniperplug" / "bot.py"


def test_global_runtime_inherits_the_single_native_implementation() -> None:
    bot_source = BOT.read_text(encoding="utf-8")
    resilient_source = RESILIENT.read_text(encoding="utf-8")
    global_source = GLOBAL.read_text(encoding="utf-8")
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\n" not in bot_source
    assert "from sniperplug.cogs.global_auto_scan_runner import AutoScanRunnerCog as GlobalAutoScanRunnerCog" in bot_source
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog as NativeAutoScanRunnerCog" in resilient_source
    assert "class AutoScanRunnerCog(NativeAutoScanRunnerCog):" in resilient_source
    assert "class AutoScanRunnerCog(resilient.AutoScanRunnerCog):" in global_source
    assert "native_auto_scan_runner_v2" not in bot_source + resilient_source + global_source


def test_review_candidates_are_counted_but_not_rendered_as_cards() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert 'review = getattr(result, "review_candidates", None)' in source
    assert "suppressed_unverified_count" in source
    assert "prepare_review_watchlist_cards" not in source
    assert "ManualReviewShareView" not in source


def test_successful_public_post_still_never_adds_private_review_cards() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "maybe_post_public_deal_cards" in source
    assert "_send_private_review_cards" not in source
    assert "_review_cards_by_guild" not in source
    assert "unverified cards shown: **0**" in source.lower()
