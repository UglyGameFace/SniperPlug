from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VariantProof:
    offer_id: str | None
    label: str | None
    attributes: dict[str, str]
    warning: str | None = None


def extract_variant_proof(item: dict[str, Any], title: str) -> VariantProof:
    attributes: dict[str, str] = {}
    for key in ("platform", "edition", "color", "size", "packSize", "model", "modelNumber"):
        value = clean_value(item.get(key))
        if value:
            attributes[key] = value

    for key in ("variantAttributes", "attributes", "selectedVariant", "swatches"):
        attributes.update(attributes_from_value(item.get(key)))

    offer_id = clean_value(item.get("offerId") or item.get("usItemId") or item.get("itemId"))
    label = variant_label(attributes)
    warning = variant_warning(title=title, attributes=attributes)

    has_variant_shape = any(key in item for key in ("variants", "productVariants", "variantAttributes", "selectedVariant", "swatches"))
    if has_variant_shape and not attributes:
        warning = "selected variant not proven; staff review required"

    return VariantProof(offer_id=offer_id, label=label, attributes=attributes, warning=warning)


def attributes_from_value(value: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if isinstance(value, dict):
        for key, raw in value.items():
            cleaned = clean_value(raw)
            if cleaned:
                attrs[normalize_attr_name(str(key))] = cleaned
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                name = clean_value(entry.get("name") or entry.get("key") or entry.get("attributeName"))
                raw = entry.get("value") or entry.get("selectedValue") or entry.get("displayName")
                cleaned = clean_value(raw)
                if name and cleaned:
                    attrs[normalize_attr_name(name)] = cleaned
    return attrs


def normalize_attr_name(name: str) -> str:
    lowered = name.strip().lower().replace(" ", "")
    aliases = {
        "compatibleplatform": "platform",
        "gamingplatform": "platform",
        "colour": "color",
        "packsize": "packSize",
        "count": "packSize",
        "modelnumber": "modelNumber",
    }
    return aliases.get(lowered, name.strip())


def variant_label(attributes: dict[str, str]) -> str | None:
    parts = []
    for key in ("platform", "edition", "color", "size", "packSize", "model", "modelNumber"):
        value = attributes.get(key)
        if value and value not in parts:
            parts.append(value)
    return " / ".join(parts[:4]) if parts else None


def variant_warning(title: str, attributes: dict[str, str]) -> str | None:
    title_text = title.lower()
    platform = (attributes.get("platform") or "").lower()
    if platform:
        if mentions_playstation(title_text) and mentions_xbox(platform):
            return "Selected option mismatch: parent listing mentions PS5 but priced variant is Xbox."
        if mentions_xbox(title_text) and mentions_playstation(platform):
            return "Selected option mismatch: parent listing mentions Xbox but priced variant is PlayStation."

    title_count = pack_count(title)
    variant_count = pack_count(attributes.get("packSize") or attributes.get("size") or "")
    if title_count and variant_count and title_count != variant_count:
        return f"Selected option mismatch: parent listing mentions {title_count} pack but priced variant is {variant_count} pack."
    return None


def mentions_playstation(text: str) -> bool:
    return "ps5" in text or "playstation" in text or "play station" in text


def mentions_xbox(text: str) -> bool:
    return "xbox" in text or "x box" in text


def pack_count(text: str) -> int | None:
    match = re.search(r"\b(\d{1,3})\s*(?:pack|pk|count|ct)\b", text.lower())
    return int(match.group(1)) if match else None


def clean_value(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip() or None
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "selectedValue"):
            cleaned = clean_value(value.get(key))
            if cleaned:
                return cleaned
    return None
