from pathlib import Path


def test_walmart_persistence_warning_is_bounded():
    source = Path("sniperplug/providers/cached_walmart.py").read_text()
    assert "Walmart persistence degraded:" in source
    assert "persistence_error_count" in source
    assert "*(f\"Walmart persistence warning:" not in source


def test_active_cache_uses_structured_logging():
    source = Path("sniperplug/services/public_deal_posts.py").read_text()
    assert 'log = logging.getLogger("sniperplug")' in source
    assert "log.exception(" in source
    section = source[source.index("async def cache_active_deal_cards"):source.index("async def ensure_public_post_tables")]
    assert "active deal cache skipped one malformed card" in section
    assert "print(" not in section
