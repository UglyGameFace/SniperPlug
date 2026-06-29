from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manual_review_share_uses_paginated_post_buttons() -> None:
    source = read("sniperplug/services/manual_review_share.py")
    assert "class ManualShareButton" in source
    assert "class ManualReviewPageButton" in source
    assert "DEFAULT_REVIEW_PAGE_SIZE = 3" in source
    assert "DEFAULT_REVIEW_MAX_CARDS = 12" in source
    assert "page_embeds" in source
    assert "edit_message" in source
    assert "discord.ButtonStyle.success" in source
    assert "share_review_card" in source
    assert "Manage Server" in source


def test_autoscan_private_review_panel_is_wired() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Private autoscan review leads" in source
    assert "ManualReviewShareView(cards" in source
    assert "view.page_embeds()" in source
    assert "self._review_cards_by_guild" in source
    assert "legacy.prepare_review_watchlist_cards(result" in source


def test_autoscan_private_review_cards_are_bounded_and_paginated() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_MANUAL_QUERY_COUNT = 8" in source
    assert "NATIVE_BROAD_PRESET_KEY" in source
    assert "NATIVE_REVIEW_CARD_LIMIT = 12" in source
    assert "NATIVE_REVIEW_PAGE_SIZE = 3" in source
    assert "NATIVE_MANUAL_TIMEOUT_SECONDS = 90" in source


def test_autoscan_load_limits_are_bounded() -> None:
    source = read("sniperplug/services/autoscan_observed_price_memory.py")
    assert "AUTOSCAN_SEARCH_CONCURRENCY = 3" in source
    assert "AUTOSCAN_PAGES_PER_QUERY = 2" in source
    assert "AUTOSCAN_MEMORY_RECHECK_LIMIT = 4" in source
    assert "AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES = 300" in source
