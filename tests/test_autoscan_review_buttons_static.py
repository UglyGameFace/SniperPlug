from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manual_review_share_component_remains_available_for_explicit_workflows() -> None:
    source = read("sniperplug/services/manual_review_share.py")
    assert "class ManualShareButton" in source
    assert "class ManualReviewPageButton" in source
    assert "DEFAULT_REVIEW_PAGE_SIZE = 3" in source
    assert "DEFAULT_REVIEW_MAX_CARDS = 12" in source
    assert "page_embeds" in source
    assert "await interaction.response.defer()" in source
    assert "edit_original_response" in source
    assert "interaction.followup.send" in source
    assert "discord.ButtonStyle.success" in source
    assert "share_review_card" in source
    assert "Manage Server" in source


def test_autoscan_does_not_send_private_review_cards() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Private autoscan review leads" not in source
    assert "ManualReviewShareView" not in source
    assert "_review_cards_by_guild" not in source
    assert "prepare_review_watchlist_cards" not in source
    assert "_send_private_review_cards" not in source
    assert "unverified cards shown: **0**" in source.lower()
    assert "Anything uncertain is suppressed and never shown as a deal." in source


def test_autoscan_manual_output_is_exact_verified_only() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_MANUAL_QUERY_COUNT = 8" in source
    assert "NATIVE_BROAD_PRESET_KEY" in source
    assert "NATIVE_MANUAL_TIMEOUT_SECONDS = 90" in source
    assert 'public_mode="Exact-Verified Deals Only"' in source
    assert "Why no verified deal was shown" in source
    assert "Search hints and review-only candidates are never displayed as deals." in source
    assert "used_repeat_fallback=False" in source


def test_autoscan_load_limits_are_bounded() -> None:
    source = read("sniperplug/services/autoscan_observed_price_memory.py")
    assert "AUTOSCAN_SEARCH_CONCURRENCY = 3" in source
    assert "AUTOSCAN_PAGES_PER_QUERY = 2" in source
    assert "AUTOSCAN_MEMORY_RECHECK_LIMIT = 4" in source
    assert "AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES = 300" in source
