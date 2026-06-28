from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manual_review_share_uses_post_buttons() -> None:
    source = read("sniperplug/services/manual_review_share.py")
    assert "class ManualShareButton" in source
    assert "discord.ButtonStyle.success" in source
    assert "share_review_card" in source
    assert "Manage Server" in source


def test_autoscan_private_review_panel_is_wired() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Private autoscan review leads" in source
    assert "ManualReviewShareView(cards)" in source
    assert "_private_review_cards_for_report" in source
    assert "build_review_candidate_cards" in source


def test_autoscan_private_review_scan_is_bounded() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "PRIVATE_AUTOSCAN_REVIEW_QUERY_LIMIT = 3" in source
    assert "PRIVATE_AUTOSCAN_REVIEW_MAX_RESULTS = 12" in source
    assert "PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT = 3" in source


def test_autoscan_load_limits_are_bounded() -> None:
    source = read("sniperplug/services/autoscan_observed_price_memory.py")
    assert "AUTOSCAN_SEARCH_CONCURRENCY = 3" in source
    assert "AUTOSCAN_PAGES_PER_QUERY = 2" in source
    assert "AUTOSCAN_MEMORY_RECHECK_LIMIT = 4" in source
    assert "AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES = 300" in source
