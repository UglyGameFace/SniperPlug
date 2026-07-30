from __future__ import annotations

import hashlib
import json
from typing import Any


_VARIANT_ATTRIBUTE_KEYS = (
    "optionId",
    "offerId",
    "sellerId",
    "seller",
    "fulfillment",
    "condition",
    "platform",
    "size",
    "color",
    "packSize",
    "unitSize",
    "model",
    "modelNumber",
)


def derived_variant_identity(
    *,
    variant_label: Any = None,
    variant_attributes: dict[str, Any] | None = None,
    pack_size: Any = None,
    color: Any = None,
    platform: Any = None,
    model: Any = None,
    seller_name: Any = None,
    fulfillment_type: Any = None,
    condition: Any = None,
) -> str | None:
    attrs = variant_attributes or {}
    payload = {
        "variant_label": _clean(variant_label),
        "pack_size": _clean(pack_size),
        "color": _clean(color),
        "platform": _clean(platform),
        "model": _clean(model),
        "seller_name": _clean(seller_name),
        "fulfillment_type": _clean(fulfillment_type),
        "condition": _clean(condition),
        "attributes": {key: _clean(attrs.get(key)) for key in _VARIANT_ATTRIBUTE_KEYS if _clean(attrs.get(key))},
    }
    payload = {key: value for key, value in payload.items() if value}
    if not payload:
        return None
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "variant:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _clean(value: Any) -> str | None:
    text = " ".join(str(value or "").strip().lower().split())
    return text or None
