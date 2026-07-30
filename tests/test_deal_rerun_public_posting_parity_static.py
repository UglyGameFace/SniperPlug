from pathlib import Path


def test_rerun_uses_public_selection_and_actual_threshold():
    source = Path("sniperplug/cogs/deal_scanner.py").read_text()
    start = source.index("async def _rerun")
    end = source.index("async def _hunt_pages", start)
    block = source[start:end]
    assert "public_cards = select_public_deal_candidates(" in block
    assert "min_discount=shown_discount" in block
    assert "cards=public_cards" in block
    assert "min_public_discount=shown_discount" in block
    assert "cards=shown_cards" not in block
