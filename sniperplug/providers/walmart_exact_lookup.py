from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

from sniperplug.providers.walmart import WalmartProviderError


AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE = "affiliate_product_lookup_exact_item"
AFFILIATE_PRODUCT_LOOKUP_ROUTE = "items_ids_v274"

_WRAPPER_KEYS = (
    "item",
    "product",
    "data",
    "items",
    "products",
    "itemResponse",
    "results",
)
_MAX_EXACT_LOOKUP_NODES = 2_000
_MAX_EXACT_LOOKUP_DEPTH = 8

_PRICE_DIAGNOSTIC_KEY_FRAGMENTS = (
    "price",
    "amount",
    "sale",
    "was",
    "regular",
    "original",
    "cost",
)
_PRICE_DIAGNOSTIC_OFFER_KEYS = frozenset(
    {
        "selectedoffer",
        "selected_offer",
        "selectedoffers",
        "offers",
        "offer",
        "buybox",
        "buy_box",
    }
)
_PRICE_DIAGNOSTIC_MAX_NODES = 400
_PRICE_DIAGNOSTIC_MAX_DEPTH = 6
_PRICE_DIAGNOSTIC_MAX_FIELDS = 12
_PRICE_DIAGNOSTIC_MAX_OFFER_SHAPES = 4
_PRICE_DIAGNOSTIC_MAX_CHARS = 320


async def fetch_exact_affiliate_product_lookup_payload(
    provider: Any,
    item_id: str,
) -> dict:
    """Fetch one exact Walmart item through Affiliate Product Lookup.

    The Affiliate Search endpoint is a relevance search even when its ``query``
    text happens to be numeric. Exact recovery therefore uses Product Lookup's
    ``ids`` parameter and still verifies the returned Walmart item ID before any
    payload can reach the canonical exact verifier.
    """

    return await asyncio.to_thread(
        _fetch_exact_affiliate_product_lookup_payload_sync,
        provider,
        item_id,
    )


def _fetch_exact_affiliate_product_lookup_payload_sync(
    provider: Any,
    item_id: str,
) -> dict:
    clean_id = str(item_id or "").strip()
    if not clean_id:
        raise WalmartProviderError(
            "Walmart Affiliate Product Lookup skipped: missing item id.",
            retry_kind="api_exact_lookup_missing_id",
        )

    detail_url = str(getattr(provider, "detail_url", "") or "").strip()
    request_json = getattr(provider, "_request_json", None)
    if not detail_url or not callable(request_json):
        raise WalmartProviderError(
            "Walmart Affiliate Product Lookup is unavailable on this provider.",
            retry_kind="api_exact_lookup_unavailable",
        )

    params = {
        "ids": clean_id,
        "responseGroup": "full",
    }
    config = getattr(provider, "config", None)
    publisher_id = str(getattr(config, "publisher_id", "") or "").strip()
    if publisher_id:
        params["publisherId"] = publisher_id

    url = f"{detail_url}?{urllib.parse.urlencode(params)}"
    payload = request_json(url)
    item = exact_item_from_product_lookup_payload(payload, clean_id)
    if item is None:
        raise WalmartProviderError(
            f"Walmart Affiliate Product Lookup returned no exact item match for {clean_id}.",
            retry_kind="api_exact_lookup_no_match",
        )

    exact = dict(item)
    exact["sniperplugOfficialExactSource"] = AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE
    exact["sniperplugOfficialExactRoute"] = AFFILIATE_PRODUCT_LOOKUP_ROUTE
    return exact


def exact_item_from_product_lookup_payload(payload: Any, item_id: str) -> dict | None:
    """Return only the requested item from bounded Product Lookup wrappers."""

    clean_id = str(item_id or "").strip()
    if not clean_id:
        return None

    stack: list[tuple[Any, int]] = [(payload, 0)]
    visited = 0
    while stack and visited < _MAX_EXACT_LOOKUP_NODES:
        node, depth = stack.pop()
        visited += 1
        if depth > _MAX_EXACT_LOOKUP_DEPTH:
            continue

        if isinstance(node, dict):
            returned_id = node.get("itemId") or node.get("usItemId")
            if str(returned_id or "").strip() == clean_id:
                return node
            for key in _WRAPPER_KEYS:
                child = node.get(key)
                if isinstance(child, (dict, list)):
                    stack.append((child, depth + 1))
            continue

        if isinstance(node, list):
            for child in reversed(node):
                if isinstance(child, (dict, list)):
                    stack.append((child, depth + 1))

    return None


def summarize_product_lookup_price_shape(payload: Any, item_id: str) -> str:
    """Return a bounded field-shape diagnostic for one exact Product Lookup item.

    This is intentionally observability-only. It never chooses a price, changes a
    candidate, or broadens trusted proof. The summary contains field paths,
    compact scalar values/types for price-like keys, and selected-offer container
    key shapes so production can distinguish an unrecognized valid price path
    from an exact item that simply has no usable current price.
    """

    item = exact_item_from_product_lookup_payload(payload, item_id)
    if item is None:
        return "item=missing"

    item_keys = [_diagnostic_token(key) for key in list(item.keys())[:16]]
    price_fields: list[str] = []
    offer_shapes: list[str] = []
    stack: list[tuple[Any, str, int]] = [(item, "", 0)]
    visited = 0

    while stack and visited < _PRICE_DIAGNOSTIC_MAX_NODES:
        node, path, depth = stack.pop()
        visited += 1
        if depth > _PRICE_DIAGNOSTIC_MAX_DEPTH:
            continue

        if isinstance(node, dict):
            for raw_key, child in reversed(list(node.items())):
                key = str(raw_key or "")
                clean_key = _diagnostic_token(key)
                child_path = f"{path}.{clean_key}" if path else clean_key
                lowered = key.strip().lower()

                if (
                    len(price_fields) < _PRICE_DIAGNOSTIC_MAX_FIELDS
                    and any(fragment in lowered for fragment in _PRICE_DIAGNOSTIC_KEY_FRAGMENTS)
                ):
                    price_fields.append(
                        f"{child_path}:{_diagnostic_value(child)}"
                    )

                if (
                    len(offer_shapes) < _PRICE_DIAGNOSTIC_MAX_OFFER_SHAPES
                    and lowered.replace("-", "_") in _PRICE_DIAGNOSTIC_OFFER_KEYS
                ):
                    offer_shapes.append(
                        f"{child_path}:{_diagnostic_container_shape(child)}"
                    )

                if isinstance(child, (dict, list)):
                    stack.append((child, child_path, depth + 1))
            continue

        if isinstance(node, list):
            for index in range(len(node) - 1, -1, -1):
                child = node[index]
                if isinstance(child, (dict, list)):
                    child_path = f"{path}[{index}]" if path else f"[{index}]"
                    stack.append((child, child_path, depth + 1))

    parts = ["keys=" + ",".join(value for value in item_keys if value)]
    parts.append("prices=" + ("|".join(price_fields) if price_fields else "none"))
    parts.append("offers=" + ("|".join(offer_shapes) if offer_shapes else "none"))
    summary = ";".join(parts)
    return summary[:_PRICE_DIAGNOSTIC_MAX_CHARS].rstrip(";|,")


def _diagnostic_container_shape(value: Any) -> str:
    if isinstance(value, dict):
        keys = [_diagnostic_token(key) for key in list(value.keys())[:10]]
        return "dict[" + ",".join(key for key in keys if key) + "]"
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            keys = [_diagnostic_token(key) for key in list(value[0].keys())[:8]]
            return f"list{len(value)}[" + ",".join(key for key in keys if key) + "]"
        return f"list{len(value)}"
    return type(value).__name__


def _diagnostic_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool:true" if value else "bool:false"
    if isinstance(value, (int, float)):
        return f"{type(value).__name__}:{value}"
    if isinstance(value, str):
        compact = _diagnostic_token(value, max_chars=36)
        return f"str:{compact or 'empty'}"
    return _diagnostic_container_shape(value)


def _diagnostic_token(value: Any, *, max_chars: int = 48) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    safe = []
    for char in text:
        if char.isalnum() or char in "._-/:[]":
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe)[:max_chars]


__all__ = [
    "AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE",
    "AFFILIATE_PRODUCT_LOOKUP_ROUTE",
    "exact_item_from_product_lookup_payload",
    "fetch_exact_affiliate_product_lookup_payload",
    "summarize_product_lookup_price_shape",
]
