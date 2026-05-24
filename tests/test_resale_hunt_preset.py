from sniperplug.cogs import deal_scanner
from sniperplug.services.resale_hunt import RESALE_HUNT_KEY, RESALE_HUNT_QUERIES, install_resale_hunt_preset


def test_install_resale_hunt_preset_adds_resale_button_preset():
    install_resale_hunt_preset()

    preset = deal_scanner.HUNT_PRESETS[RESALE_HUNT_KEY]

    assert preset.label == "Resale Hunt"
    assert preset.emoji == "♻️"
    assert preset.min_discount == 25
    assert preset.queries == RESALE_HUNT_QUERIES


def test_resale_queries_cover_flip_friendly_conditions():
    combined = " ".join(RESALE_HUNT_QUERIES).lower()

    assert "restored" in combined
    assert "refurbished" in combined
    assert "open box" in combined
