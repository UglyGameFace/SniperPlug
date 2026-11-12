from __future__ import annotations

"""Curated product targets, distinct from observed sales or retailer availability.

This only influences *ordering* within existing verified/search candidate paths.
It must never serve as price, stock, seller, resale, sales or variant proof.
We use the item's title, not descriptions, Discord embeds, search routes or
keywords supplied by users in free-form messages.
"""

from dataclasses import dataclass
import re
from typing import Any


# Family terms are intentionally specific enough that common accessories
# cannot masquerade as a sought-after device. More categories can be added
# with labeled positive/negative examples, not a broad "expensive brand" guess.
_FAMILY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("game_controller", r"\b(?:dualsense|dual\s*sense|joy[\s-]?con|xbox\s*(?:wireless\s*)?controller|(?:ps5|playstation\s*5|nintendo\s*switch)\s*(?:wireless\s*)?controller)\b"),
    ("game_console", r"\b(?:playstation\s*5|ps5|xbox\s*series\s*[xs]|nintendo\s*switch(?:\s*2)?|steam\s*deck)\b"),
    ("graphics_card", r"\b(?:geforce\s*(?:rtx|gtx)|rtx\s*[2345]\d{3}|radeon\s*rx\s*[5679]\d{3}|graphics\s*card|\bgpu\b)"),
    ("gaming_monitor", r"\b(?:gaming\s*monitor|(?:oled|qled|4k|144hz|165hz|240hz|360hz|\d{2}\s*(?:inch|in))\s*(?:gaming\s*)?monitor|monitor\s*(?:oled|qled|4k|144hz|240hz))\b"),
    ("television", r"\b(?:oled\s*tv|qled\s*tv|\d{2}\s*(?:inch|in)\s*(?:4k\s*)?(?:smart\s*)?tv|smart\s*tv|4k\s*television)\b"),
    ("laptop", r"\b(?:gaming\s*laptop|macbook\s*(?:air|pro)|(?:asus|lenovo|dell|hp|acer|msi|razer)\s*(?:gaming\s*)?laptop)\b"),
    ("phone", r"\b(?:iphone\s*(?:1[1-9]|2\d)|samsung\s*galaxy\s*s\d{2}|google\s*pixel\s*\d{1,2})\b"),
    ("tablet", r"\b(?:ipad\s*(?:air|pro|mini|\d{1,2})|galaxy\s*tab\s*s\d{1,2})\b"),
    ("vr_headset", r"\b(?:meta\s*quest\s*[234]|oculus\s*quest\s*[23])\b"),
)

# When the title describes a peripheral instead of the device, block the
# target bonus even when it contains the expensive product's exact name.
_ACCESSORY = re.compile(
    r"\b(?:case|cases|cover|covers|skin|skins|screen\s*protector|"
    r"tempered\s*glass|mount|bracket|stand|replacement\s*(?:part|parts|"
    r"battery|fan|screen|shell|case)|repair\s*(?:kit|parts)|adapter|"
    r"charging\s*(?:dock|stand|cable)|charge\s*cable|cable|"
    r"protective\s*sleeve|carrying\s*bag|carry\s*bag|storage\s*bag|"
    r"cooling\s*(?:pad|fan)|dust\s*cover|stickers?|decals?|"
    r"thumb\s*grips?|display\s*screen\s*only|water\s*block|"
    r"waterblock|heatsink|thermal\s*pad|cooling\s*kit|"
    r"service\s*remote|remote\s*control)\b",
    flags=re.IGNORECASE,
)

# Bundle safeguards: console + supplied charger/case is still a console,
# but "console case bundle" without a console item is an accessory.
_NON_HARDWARE_SOFTWARE = re.compile(
    r"\b(?:video\s*game|game\s*(?:card|cartridge|disc|download|code)|"
    r"digital\s*(?:code|download|game|gift|edition)|"
    r"software\s*(?:key|license)|game\s*pass|subscription|"
    r"gift\s*card|download\s*card|season\s*pass|"
    r"dlc|game\s*only|replacement\s*shell)\b",
    flags=re.IGNORECASE,
)

_DEVICE_EXPLICIT = re.compile(
    r"\b(?:console|system|handheld\s*(?:gaming\s*)?pc|"
    r"gaming\s*laptop|graphics\s*card|gpu|monitor|television|"
    r"smart\s*tv|phone|tablet|headset|controller)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ProductTargetFit:
    family: str = ""
    tier: str = "unclassified"
    rank_points: int = 0
    reason: str = "Title insufficient for curated product target; buyer demand unknown"


def _item_title(record: Any) -> str:
    # Never scan embed text as a proxy for the actual product title.
    for obj in (
        getattr(record, "deal", None),
        getattr(record, "candidate", None),
        record,
    ):
        if obj is None:
            continue
        for name in ("title", "label"):
            value = getattr(obj, name, None)
            if isinstance(value, str) and value.strip():
                return " ".join(value.lower().split())[:300]
    return ""


def assess_product_target_fit(record: Any) -> ProductTargetFit:
    title = _item_title(record)
    if not title:
        return ProductTargetFit()
    family = next(
        (key for key, pattern in _FAMILY_PATTERNS if re.search(pattern, title)),
        "",
    )
    if not family:
        return ProductTargetFit()

    # Digital software and game discs are not game consoles simply because
    # their titles say PS5/Switch/Xbox. A retail console with an included game
    # must explicitly say "console bundle with" or "system bundle with".
    explicit_console_bundle = bool(
        re.search(
            r"\b(?:console|system)\s+bundle\s+(?:with|including)\b",
            title,
        )
    )
    if _NON_HARDWARE_SOFTWARE.search(title) and not explicit_console_bundle:
        return ProductTargetFit(
            family, "accessory", -70,
            "Game/software/digital listing is not the physical target device",
        )

    if _ACCESSORY.search(title):
        # Cases/chargers bundled with an explicitly described console are
        # acceptable; "console case bundle with cables" is not.
        complete_bundle = family == "game_console" and explicit_console_bundle
        if not complete_bundle:
            return ProductTargetFit(
                family, "accessory", -70,
                "Title matches accessory/part for a target product, not the device",
            )

    return ProductTargetFit(
        family, "curated_target", 65,
        f"Curated {family.replace('_', ' ')} target; sales demand not measured",
    )
