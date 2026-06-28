from __future__ import annotations

from sniperplug.cogs.deal_scanner import HuntPreset


OPEN_BOX_AUTOSCAN_KEY = "open_box"
OPEN_BOX_AUTOSCAN_LABEL = "Open Box / Like-New"
OPEN_BOX_AUTOSCAN_EMOJI = "📦"
OPEN_BOX_AUTOSCAN_DESCRIPTION = "Condition markdown lanes: open-box, like-new, restored, refurbished, and pre-owned Walmart offers."
OPEN_BOX_AUTOSCAN_MIN_DISCOUNT = 50

OPEN_BOX_AUTOSCAN_QUERIES: tuple[str, ...] = (
    "open box vacuum",
    "open box electronics",
    "restored vacuum",
    "refurbished vacuum",
    "like new vacuum",
    "open box appliance",
    "restored electronics",
    "open box home",
    "open box gaming",
    "open box monitor",
    "open box laptop",
)


def open_box_autoscan_preset() -> HuntPreset:
    return HuntPreset(
        OPEN_BOX_AUTOSCAN_KEY,
        OPEN_BOX_AUTOSCAN_LABEL,
        OPEN_BOX_AUTOSCAN_EMOJI,
        OPEN_BOX_AUTOSCAN_DESCRIPTION,
        OPEN_BOX_AUTOSCAN_QUERIES,
        OPEN_BOX_AUTOSCAN_MIN_DISCOUNT,
    )
