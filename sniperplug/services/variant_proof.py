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

    selected_offer_id = clean_value(item.get("offerId") or item.get("usItemId") or item.get("itemId"))
    variant_list_attrs, variant_offer_id = selected_variant_from_lists(item, selected_offer_id)
    attributes.update(variant_list_attrs)
    offer_id = selected_offer_id or variant_offer_id

    label = variant_label(attributes)
    warning = variant_warning(title=title, attributes=attributes)

    has_variant_shape = any(key in item for key in ("variants", "productVariants", "variantAttributes", "selectedVariant", "swatches"))
    if has_variant_shape and not attributes:
        warning = "selected variant not proven; staff review required"

    return VariantProof(offer_id=offer_id, label=label, attributes=attributes, warning=warning)


def selected_variant_from_lists(item: dict[str, Any], selected_offer_id: str | None) -> tuple[dict[str, str], str | None]:
    for key in ("productVariants", "variants"):
        value = item.get(key)
        if not isinstance(value, list):
            continue
        selected = select_variant_entry(value, selected_offer_id)
        if selected is None:
            continue
        offer_id = clean_value(selected.get("offerId") or selected.get("usItemId") or selected.get("itemId") or selected.get("id"))
        attrs = attributes_from_value(selected)
        attrs.update(attributes_from_value(selected.get("variantAttributes")))
        attrs.update(attributes_from_value(selected.get("attributes")))
        return attrs, offer_id
    return {}, None


def select_variant_entry(entries: list[Any], selected_offer_id: str | None) -> dict[str, Any] | None:
    dict_entries = [entry for entry in entries if isinstance(entry, dict)]
    if not dict_entries:
        return None
    if selected_offer_id:
        for entry in dict_entries:
            ids = {
                clean_value(entry.get("offerId")),
                clean_value(entry.get("usItemId")),
                clean_value(entry.get("itemId")),
                clean_value(entry.get("id")),
            }
            if selected_offer_id in ids:
                return entry
    selected_markers = ("selected", "isSelected", "default", "isDefault", "current", "isCurrent")
    for entry in dict_entries:
        if any(entry.get(marker) is True for marker in selected_markers):
            return entry
    return None


def attributes_from_value(value: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if isinstance(value, dict):
        for key, raw in value.items():
            if key in {"variants", "productVariants"}:
                continue
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
    return filter_variant_attributes(attrs)


def filter_variant_attributes(attrs: dict[str, str]) -> dict[str, str]:
    allowed = {"platform", "edition", "color", "size", "packSize", "model", "modelNumber"}
    return {key: value for key, value in attrs.items() if key in allowed and value}


def normalize_attr_name(name: str) -> str:
    lowered = name.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    aliases = {
        "platform": "platform",
        "compatibleplatform": "platform",
        "gamingplatform": "platform",
        "edition": "edition",
        "color": "color",
        "colour": "color",
        "actualcolor": "color",
        "size": "size",
        "productsize": "size",
        "packsize": "packSize",
        "pack": "packSize",
        "count": "packSize",
        "model": "model",
        "modelnumber": "modelNumber",
        "manufacturerpartnumber": "modelNumber",
    }
    return aliases.get(lowered, name.strip())


def variant_label(attributes: dict[str, str]) -> str | None:
    """Build a customer-facing selected option label.

    Model numbers are useful proof, but they are not option labels. Keeping them
    out avoids ugly labels like "Multicolor / 100 oz / 50597" while still
    preserving model proof in attributes/footer.
    """
    parts = []
    for key in ("packSize", "size", "platform", "edition", "color"):
        value = attributes.get(key)
        if value and value not in parts:
            parts.append(value)
    return " / ".join(parts[:4]) if parts else None


def variant_warning(title: str, attributes: dict[str, str]) -> str | None:
    title_text = title.lower()
    platform = (attributes.get("platform") or "").lower()
    title_has_ps = mentions_playstation(title_text)
    title_has_xbox = mentions_xbox(title_text)
    if platform and title_has_ps != title_has_xbox:
        if title_has_ps and mentions_xbox(platform):
            return "Selected option mismatch: parent listing mentions PS5 but priced variant is Xbox."
        if title_has_xbox and mentions_playstation(platform):
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
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip() or None
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "selectedValue"):
            cleaned = clean_value(value.get(key))
            if cleaned:
                return cleaned
    return None
