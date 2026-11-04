from __future__ import annotations

"""Short-lived cache of exact Walmart Product Lookup items fetched in batches.

Walmart's Affiliate Product Lookup accepts up to 20 comma-separated item IDs
per request. The exact-verification worker prefetches its claimed items in
one call per 20 IDs; the existing one-item lookup paths then take their item
from this cache instead of spending one API call each.

Entries are matched by exact returned item ID, used at most once, and expire
quickly, so a cached item is never older than one worker batch. A miss simply
falls through to the original single-item request.
"""

import copy
import threading
import time
from typing import Any

BATCH_LOOKUP_MAX_IDS = 20
BATCH_CACHE_TTL_SECONDS = 90.0
BATCH_CACHE_MAX_ENTRIES = 400

_CACHE_ATTR = "_sniperplug_exact_lookup_batch_cache"
_LOCK = threading.Lock()


def _cache(provider: Any, *, create: bool) -> dict[str, tuple[float, dict]] | None:
    cache = getattr(provider, _CACHE_ATTR, None)
    if isinstance(cache, dict):
        return cache
    if not create:
        return None
    cache = {}
    try:
        setattr(provider, _CACHE_ATTR, cache)
    except Exception:  # noqa: BLE001 - provider doubles may forbid attributes.
        return None
    return cache


def store_prefetched_items(provider: Any, items: dict[str, dict]) -> int:
    """Remember exact items keyed by Walmart item ID. Returns entries stored."""
    if not items:
        return 0
    now = time.monotonic()
    with _LOCK:
        cache = _cache(provider, create=True)
        if cache is None:
            return 0
        for key in [k for k, (expires, _item) in cache.items() if expires <= now]:
            cache.pop(key, None)
        stored = 0
        for item_id, item in items.items():
            clean = str(item_id or "").strip()
            if not clean or not isinstance(item, dict):
                continue
            cache[clean] = (now + BATCH_CACHE_TTL_SECONDS, item)
            stored += 1
        while len(cache) > BATCH_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)), None)
    return stored


def take_prefetched_item(provider: Any, item_id: Any) -> dict | None:
    """Return (and consume) a fresh prefetched copy of exactly this item."""
    clean = str(item_id or "").strip()
    if not clean:
        return None
    with _LOCK:
        cache = _cache(provider, create=False)
        if not cache:
            return None
        entry = cache.pop(clean, None)
    if entry is None:
        return None
    expires, item = entry
    if expires <= time.monotonic():
        return None
    returned_id = str(item.get("itemId") or item.get("usItemId") or "").strip()
    if returned_id != clean:
        return None
    return copy.deepcopy(item)


def clear_prefetched_items(provider: Any) -> None:
    with _LOCK:
        cache = _cache(provider, create=False)
        if cache is not None:
            cache.clear()
