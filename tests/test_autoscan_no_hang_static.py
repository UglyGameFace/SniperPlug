from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_native_autoscan_manual_pass_is_bounded() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_MANUAL_QUERY_COUNT = 6" in source
    assert "NATIVE_MANUAL_TIMEOUT_SECONDS = 90" in source
    assert "query_count_override=NATIVE_MANUAL_QUERY_COUNT" in source
    assert "Deep follow-up" not in source


def test_native_autoscan_uses_same_result_review_cards() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "review_cards = legacy.prepare_review_watchlist_cards(result" in source
    assert "self._review_cards_by_guild" in source
    assert "same autoscan pass" in source
    assert "ManualReviewShareView" in source
    assert "run_walmart_scan" not in source


def test_private_review_panel_is_paginated() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_REVIEW_CARD_LIMIT = 12" in source
    assert "NATIVE_REVIEW_PAGE_SIZE = 3" in source
