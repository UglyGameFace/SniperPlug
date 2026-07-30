from pathlib import Path


def test_preset_hunt_posts_against_displayed_threshold():
    source = Path("sniperplug/cogs/deal_scanner.py").read_text()
    class_start = source.index("class HuntPresetButton")
    post_start = source.index("public_cards = select_public_deal_candidates(", class_start)
    post_end = source.index("add_public_posting_field(summary, public_result)", post_start)
    posting_block = source[post_start:post_end]
    assert "min_discount=shown_discount" in posting_block
    assert "min_public_discount=shown_discount" in posting_block
    assert "min_discount=self.preset.min_discount" not in posting_block
    assert "min_public_discount=self.preset.min_discount" not in posting_block
