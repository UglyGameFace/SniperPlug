from pathlib import Path


def test_public_posting_gate_enforces_category_preferences():
    source = Path("sniperplug/services/public_deal_posts.py").read_text()
    assert "category_preferences = await get_category_preferences(db, guild_id)" in source
    assert "if category_decision.action == \"suppress\":" in source
    assert "public category preference read failed; posting blocked" in source
    assert source.index("if category_decision.action == \"suppress\":") < source.index("reserve_public_deal_post(")
