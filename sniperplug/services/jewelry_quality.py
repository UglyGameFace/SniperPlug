from __future__ import annotations

"""Precious-metal evidence gate for *deal alerts*, not an authenticity certificate.

Fashion metal finishes, simulated stones and unknown materials are not
investment/precious jewelry. Explicit metal claims are source statements, not
independent lab verification. Keep discovery broad and filter at delivery.
"""

import re
from typing import Any

_JEWELRY = re.compile(
    r"\b(?:jewel(?:ry|lery)|necklaces?|pendants?|bracelets?|earrings?|"
    r"anklets?|bangles?|charms?|brooches?|lockets?|chains?|"
    r"engagement rings?|wedding rings?|promise rings?)\b",
    re.IGNORECASE,
)
_RING = re.compile(r"\brings?\b", re.IGNORECASE)
_NON_JEWELRY_RING = re.compile(
    r"\b(?:ring\s+(?:light|doorbell|camera|alarm|binder|stand|holder)|"
    r"(?:phone|key|towel|napkin|shower curtain)\s+rings?)\b",
    re.IGNORECASE,
)
_NON_JEWELRY_CHAIN = re.compile(
    r"\b(?:key\s*chains?|chain\s*saws?|bicycle\s*chains?|snow\s*chains?|"
    r"chain\s*locks?|chain\s*link|necklace\s*(?:stand|display|holder))\b",
    re.IGNORECASE,
)
# Explicit plating/finish/costume claims override any "14K" keywords elsewhere.
_NOT_PRECIOUS = re.compile(
    r"\b(?:gold[\s-]?(?:tone|toned|color|colou?r|finish|plated|filled|overlay)|"
    r"silver[\s-]?(?:tone|toned|colou?r|color|plated|filled|overlay)|"
    r"(?:gold|silver|platinum|rhodium)[\s-]?(?:plated|plate|coated|dipped)|"
    r"vermeil|electroplat(?:ed|ing)|flash[\s-]?plat(?:ed|ing)|"
    r"cubic\s*zirconia|cz\s*(?:stones?|diamonds?)|"
    r"simulated\s*(?:diamonds?|stones?|gems?)|"
    r"faux|fake|costume|imitation|plastic|acrylic|resin|rhinestones?|"
    r"base\s*metal|zinc\s*alloy|alloy\s*jewelry|"
    r"stainless\s*steel|titanium\s*steel)\b",
    re.IGNORECASE,
)
_PRECIOUS_METAL = re.compile(
    r"\b(?:(?:solid|genuine|real|pure|fine)\s+"
    r"(?:10|14|18|22|24)\s*(?:k|kt|karat|carat)\s*gold|"
    r"(?:10|14|18|22|24)\s*(?:k|kt|karat|carat)\s*(?:solid\s*)?gold|"
    r"solid\s*gold|"
    r"(?:(?:925|\.925)\s*(?:sterling\s*)?silver|sterling\s*silver|925)\b|"
    r"(?:solid|pure|950|pt950)\s*platinum|"
    r"platinum\s*(?:950|pt950)|"
    r"(?:solid|pure)\s*palladium)\b",
    re.IGNORECASE,
)
_MATERIAL_KEYS = {
    "material", "materialtype", "metal", "metaltype", "jewelrymaterial",
    "metalpurity", "metalstamp", "primarymaterial", "fabricmaterial",
}


def jewelry_quality_rejection_reason(
    title: str, *, attributes: dict[str, Any] | None = None
) -> str | None:
    """Block alerts for clearly fashion or unsubstantiated fine jewelry.

    None means *not rejected*, not lab-verified authenticity. Only inspect
    source product-material fields, never seller marketing or product color.
    """
    name = " ".join(str(title or "").split())
    attrs = attributes if isinstance(attributes, dict) else {}
    material = " ".join(
        str(value or "")
        for key, value in attrs.items()
        if re.sub(r"[^a-z]", "", str(key).lower()) in _MATERIAL_KEYS
        and not isinstance(value, (dict, list, tuple))
    )
    category = " ".join(
        str(attrs.get(k) or "")
        for k in ("category", "categoryPath", "productCategory", "productType", "department")
    )
    jewelry_context = bool(
        _JEWELRY.search(name) or _JEWELRY.search(category) or
        (_RING.search(name) and not _NON_JEWELRY_RING.search(name))
    )
    if not jewelry_context:
        return None
    if _NON_JEWELRY_CHAIN.search(name) and not _JEWELRY.search(category):
        return None
    composition = f"{name} {material}"
    if _NOT_PRECIOUS.search(composition):
        return "fashion_or_plated_jewelry"
    if not _PRECIOUS_METAL.search(composition):
        return "precious_jewelry_material_unproven"
    return None


def card_jewelry_rejection_reason(card: Any) -> str | None:
    attrs = getattr(card, "variant_attributes", None)
    if not isinstance(attrs, dict):
        attrs = getattr(getattr(card, "candidate", None), "variant_attributes", None)
    label = str(getattr(card, "label", "") or "")
    embed = getattr(card, "embed", None)
    title = str(getattr(embed, "title", "") or "")
    # Keep full underlying product title; short button labels may truncate it.
    deal = getattr(card, "deal", None)
    candidate = getattr(card, "candidate", None)
    title = str(getattr(deal, "title", "") or getattr(candidate, "title", "") or title or label)
    return jewelry_quality_rejection_reason(title, attributes=attrs)
