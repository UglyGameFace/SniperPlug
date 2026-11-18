from pathlib import Path

from sniperplug.cogs.auto_scan_runner import watchlist_repeat_summary
from sniperplug.services.public_deal_posts import PublicPostResult


def test_watchlist_summary_does_not_claim_posted_without_public_result():
    summary = watchlist_repeat_summary("fresh filter: none", [object(), object()], None)

    assert "selected **2**" in summary
    assert "posted **2**" not in summary


def test_watchlist_summary_uses_real_public_post_result():
    result = PublicPostResult(attempted=3, posted=0, skipped_not_alertable=3)
    summary = watchlist_repeat_summary("fresh filter: none", [object(), object(), object()], result)

    assert "selected **3**" in summary
    assert "public posted **0**" in summary
    assert "public blocked **3**" in summary


def test_health_explains_cache_and_clear_helper_exists():
    source = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")

    assert "active cached deals are remembered product cards, not Discord posts" in source
    # The /autoscan_clear_cache command was removed in the 2026-11-18
    # dead-feature cleanup: its cog was never loaded, so the command never
    # ran in prod. The clear helpers remain as module utilities.
    assert 'name="autoscan_clear_cache"' not in source
    assert "clear_active_cached_deals" in source
    assert "clear_autoscan_posting_memory" in source
