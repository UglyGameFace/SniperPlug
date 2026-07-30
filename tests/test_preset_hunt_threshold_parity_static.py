from pathlib import Path


def test_preset_hunt_posts_against_displayed_threshold():
    source = Path("sniperplug/cogs/deal_scanner.py").read_text()
    start = source.index("class HuntPresetButton")
    end = source.index("class WalmartCashOffersButton", start)
    block = source[start:end]
    assert "min_discount=shown_discount" in block
    assert "min_public_discount=shown_discount" in block
    assert "min_discount=self.preset.min_discount" not in block
    assert "min_public_discount=self.preset.min_discount" not in block
