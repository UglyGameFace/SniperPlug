from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_native_autoscan_disables_manual_deep_followup() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "legacy.AUTO_SCAN_DEEP_FOLLOWUP_ENABLED = False" in source
    assert "legacy.AUTO_SCAN_DEEP_QUERY_COUNT = 6" in source
    assert "legacy.AUTO_SCAN_MANUAL_QUERY_COUNT = 6" in source


def test_private_review_scan_is_small_and_paginated() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "PRIVATE_AUTOSCAN_REVIEW_QUERY_LIMIT = 2" in source
    assert "PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT = 12" in source
    assert "PRIVATE_AUTOSCAN_REVIEW_PAGE_SIZE = 3" in source
    assert "ManualReviewShareView" in source
