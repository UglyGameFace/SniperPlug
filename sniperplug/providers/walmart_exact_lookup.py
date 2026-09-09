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


__all__ = [
    "AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE",
    "AFFILIATE_PRODUCT_LOOKUP_ROUTE",
    "exact_item_from_product_lookup_payload",
    "fetch_exact_affiliate_product_lookup_payload",
]
