from pathlib import Path


def test_active_cache_writes_are_isolated_per_card():
    source = Path("sniperplug/services/public_deal_posts.py").read_text()
    start = source.index("async def cache_active_deal_cards")
    end = source.index("async def ensure_public_post_tables", start)
    block = source[start:end]
    assert "rollback = getattr(conn, \"rollback\", None)" in block
    assert block.index("await conn.commit()") < block.index("except Exception as exc:")
    assert "return cached" in block
