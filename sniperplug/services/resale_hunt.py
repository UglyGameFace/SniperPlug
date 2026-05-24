from __future__ import annotations

from sniperplug.cogs import deal_scanner


RESALE_HUNT_KEY = "resale"

RESALE_HUNT_QUERIES = (
    "restored laptop",
    "restored iphone",
    "restored tv",
    "refurbished nintendo switch",
    "open box power tool",
)


def install_resale_hunt_preset() -> None:
    """Install the resale hunt button into the existing /hunt menu.

    The base hunt menu is intentionally simple and button-driven. This installer
    adds a dedicated resale/open-box/refurbished hunt without changing the public
    slash command shape.
    """
    deal_scanner.HUNT_PRESETS[RESALE_HUNT_KEY] = deal_scanner.HuntPreset(
        RESALE_HUNT_KEY,
        "Resale Hunt",
        "♻️",
        "Open-box, restored, refurbished, and like-new leads across flip-friendly categories.",
        RESALE_HUNT_QUERIES,
        25,
    )

    if getattr(deal_scanner.HuntPresetMenuView, "_sniperplug_resale_installed", False):
        return

    def patched_init(self) -> None:
        deal_scanner.discord.ui.View.__init__(self, timeout=300)
        layout = (
            ("glitch", 0),
            (RESALE_HUNT_KEY, 0),
            ("tech", 0),
            ("essentials", 1),
            ("home", 1),
            ("toys", 1),
            ("auto_tools", 2),
        )
        for key, row in layout:
            self.add_item(deal_scanner.HuntPresetButton(deal_scanner.HUNT_PRESETS[key], row=row))

    deal_scanner.HuntPresetMenuView.__init__ = patched_init
    deal_scanner.HuntPresetMenuView._sniperplug_resale_installed = True
