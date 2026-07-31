from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Awaitable, Callable, Hashable


WALMART_RECHECK_REUSE_SECONDS = 60
WALMART_RECHECK_ERROR_REUSE_SECONDS = 8
WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS = 25
WALMART_RECHECK_CACHE_MAX_ITEMS = 512

_TRUSTWORTHY_STATUSES = {
    "unchanged",
    "deal_improved",
    "deal_weakened",
    "promotion_verified",
    "price_changed",
    "discount_unproven",
    "discount_gone",
    "unavailable",
    "identity_mismatch",
}

_item_locks: dict[Hashable, asyncio.Lock] = {}
_recent_results: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
_registry_lock = asyncio.Lock()


async def guarded_walmart_recheck(
    item_key: Hashable,
    operation: Callable[[], Awaitable[Any]],
    *,
    timeout_seconds: int = WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS,
) -> Any:
    """Collapse duplicate item rechecks and briefly reuse their result."""

    if item_key is None or item_key == "":
        return await operation()

    cached = await _cached_result(item_key)
    if cached is not None:
        return _mark_reused(cached)

    lock = await _item_lock(item_key)
    async with lock:
        cached = await _cached_result(item_key)
        if cached is not None:
            return _mark_reused(cached)

        try:
            result = await asyncio.wait_for(operation(), timeout=max(1, int(timeout_seconds)))
        except asyncio.TimeoutError:
            result = None

        if result is not None:
            await _remember_result(item_key, result)
        return result


async def _item_lock(item_key: Hashable) -> asyncio.Lock:
    async with _registry_lock:
        lock = _item_locks.get(item_key)
        if lock is None:
            lock = asyncio.Lock()
            _item_locks[item_key] = lock
        return lock


async def _cached_result(item_key: Hashable) -> Any | None:
    now = time.monotonic()
    async with _registry_lock:
        cached = _recent_results.get(item_key)
        if cached is None:
            return None
        created_at, result = cached
        status = str(getattr(result, "status", "") or "")
        ttl = WALMART_RECHECK_REUSE_SECONDS if status in _TRUSTWORTHY_STATUSES else WALMART_RECHECK_ERROR_REUSE_SECONDS
        if now - created_at > ttl:
            _recent_results.pop(item_key, None)
            return None
        _recent_results.move_to_end(item_key)
        return result


async def _remember_result(item_key: Hashable, result: Any) -> None:
    async with _registry_lock:
        _recent_results[item_key] = (time.monotonic(), result)
        _recent_results.move_to_end(item_key)
        while len(_recent_results) > WALMART_RECHECK_CACHE_MAX_ITEMS:
            evicted_key, _ = _recent_results.popitem(last=False)
            _item_locks.pop(evicted_key, None)


async def clear_walmart_recheck_guard() -> None:
    async with _registry_lock:
        _recent_results.clear()
        _item_locks.clear()


def _mark_reused(result: Any) -> Any:
    message = str(getattr(result, "message", "") or "")
    prefix = "Reused a recent exact-item Walmart recheck to avoid another provider call. "
    try:
        return replace(result, reused=True, message=prefix + message)
    except (TypeError, ValueError):
        return result
