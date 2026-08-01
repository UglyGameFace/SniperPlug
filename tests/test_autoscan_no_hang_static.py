from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_native_autoscan_manual_pass_is_bounded() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_MANUAL_QUERY_COUNT = 8" in source
    assert "NATIVE_MANUAL_TIMEOUT_SECONDS = 90" in source
    assert "query_count_override=NATIVE_MANUAL_QUERY_COUNT" in source
    assert "Deep follow-up" not in source


def test_native_autoscan_uses_same_result_without_rerunning_review_searches() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert 'review = getattr(result, "review_candidates", None)' in source
    assert "suppressed_unverified_count" in source
    assert "run_walmart_scan" not in source
    assert "prepare_review_watchlist_cards" not in source
    assert "ManualReviewShareView" not in source


def test_private_review_panel_is_not_wired_into_autoscan() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_REVIEW_CARD_LIMIT" not in source
    assert "NATIVE_REVIEW_PAGE_SIZE" not in source
    assert "Private autoscan review leads" not in source
    assert "unverified cards shown: **0**" in source.lower()
