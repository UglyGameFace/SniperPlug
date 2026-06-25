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


def install_open_box_autoscan_routes() -> None:
    """Register bounded open-box coverage with the existing verified autoscan engine.

    This intentionally does not touch Walmart Cash discovery. It only gives the
    normal Walmart markdown autoscan a dedicated condition-deal category.
    """
    from sniperplug.services import verified_discount_hunt
    from sniperplug.cogs import auto_scan_runner

    verified_discount_hunt.CATEGORY_ROUTES[OPEN_BOX_AUTOSCAN_KEY] = (
        OPEN_BOX_AUTOSCAN_LABEL,
        OPEN_BOX_AUTOSCAN_EMOJI,
        OPEN_BOX_AUTOSCAN_DESCRIPTION,
        OPEN_BOX_AUTOSCAN_QUERIES,
    )
    verified_discount_hunt.HUNT_PRESETS[OPEN_BOX_AUTOSCAN_KEY] = HuntPreset(
        OPEN_BOX_AUTOSCAN_KEY,
        OPEN_BOX_AUTOSCAN_LABEL,
        OPEN_BOX_AUTOSCAN_EMOJI,
        OPEN_BOX_AUTOSCAN_DESCRIPTION,
        OPEN_BOX_AUTOSCAN_QUERIES,
        OPEN_BOX_AUTOSCAN_MIN_DISCOUNT,
    )

    rotation = tuple(auto_scan_runner.AUTO_SCAN_CATEGORY_ROTATION)
    if OPEN_BOX_AUTOSCAN_KEY not in rotation:
        auto_scan_runner.AUTO_SCAN_CATEGORY_ROTATION = (*rotation, OPEN_BOX_AUTOSCAN_KEY)
