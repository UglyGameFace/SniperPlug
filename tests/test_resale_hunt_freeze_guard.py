from sniperplug.cogs import deal_scanner
from sniperplug.services.resale_hunt import RESALE_HUNT_KEY, ResaleHuntButton, install_resale_hunt_preset


def test_resale_hunt_installs_dedicated_button_class():
    install_resale_hunt_preset()

    view = deal_scanner.HuntPresetMenuView()
    resale_buttons = [child for child in view.children if getattr(child, "preset", None) and child.preset.key == RESALE_HUNT_KEY]

    assert resale_buttons
    assert isinstance(resale_buttons[0], ResaleHuntButton)


def test_resale_hunt_button_label_still_matches_preset():
    install_resale_hunt_preset()

    preset = deal_scanner.HUNT_PRESETS[RESALE_HUNT_KEY]
    button = ResaleHuntButton(preset, row=0)

    assert button.label == "Resale Hunt"
    assert str(button.emoji) == "♻️"
