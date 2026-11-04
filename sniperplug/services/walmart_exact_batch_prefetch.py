from __future__ import annotations

"""Prefetch a worker batch's exact Walmart items, up to 20 IDs per API call.

The exact-verification worker used to spend at least one Product Lookup call
per claimed item. Walmart's Affiliate Product Lookup accepts up to 20 item IDs
per request, so the worker now prefetches its claims in one call per 20 IDs.
The canonical per-item verifier is unchanged: it reads each item from the
short-lived cache (exact item-ID match, single use) and only makes its own
request when an item is missing from the batch response.

Prefetch is best-effort. Any failure leaves the cache empty for that chunk and
the original per-item path runs exactly as before.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from sniperplug.providers.walmart_exact_lookup import (
    _request_exact_lookup_json,
    exact_item_from_product_lookup_payload,
)
from sniperplug.providers.walmart_lookup_batch_cache import (
    BATCH_LOOKUP_MAX_IDS,
    store_prefetched_items,
)
from sniperplug.services.walmart_request_coordinator import (
    EXACT_PRIORITY,
    walmart_request_slot,
)

log = logging.getLogger("sniperplug.autoscan.walmart_exact_batch_prefetch")

PREFETCH_MIN_IDS = 2


@dataclass(frozen=True)
class ExactBatchPrefetchResult:
    requested_ids: int = 0
    api_calls: int = 0
    items_cached: int = 0
    failed_calls: int = 0

    def summary_line(self) -> str:
        return (
            f"batched lookup ids={self.requested_ids} calls={self.api_calls} "
            f"cached={self.items_cached} failed_calls={self.failed_calls}"
        )


def lookup_base_provider(provider: Any) -> Any | None:
    """Return the signing provider the per-item lookups use as their cache key.

    Production wraps it as Coordinated -> Cached -> base; ``inner`` on the
    outer wrappers resolves straight to the base. The verifier's one-item
    paths run on that same base object, so the cache must live there.
    """
    node = provider
    for _ in range(6):
        nxt = getattr(node, "inner", None)
        if nxt is None or nxt is node:
            break
        node = nxt
    # Only the real signing Walmart provider: its own one-item lookup methods
    # are the cache consumers. Other providers and test doubles keep the
    # original per-item behavior untouched.
    from sniperplug.providers.walmart import WalmartProvider

    if not isinstance(node, WalmartProvider):
        return None
    detail_url = str(getattr(node, "detail_url", "") or "").strip()
    return node if detail_url else None


def _clean_ids(item_ids: Iterable[Any]) -> list[str]:
    ordered: dict[str, None] = {}
    for value in item_ids:
        text = str(value or "").strip()
        if text.isdigit():
            ordered.setdefault(text, None)
    return list(ordered)


def batch_lookup_url(base: Any, chunk: list[str]) -> str:
    detail_url = str(getattr(base, "detail_url", "") or "").strip()
    query = f"ids={','.join(chunk)}&responseGroup=full"
    config = getattr(base, "config", None)
    publisher_id = str(getattr(config, "publisher_id", "") or "").strip()
    if publisher_id:
        from urllib.parse import quote

        query += f"&publisherId={quote(publisher_id, safe='')}"
    return f"{detail_url}?{query}"


def _fetch_chunk_sync(base: Any, chunk: list[str]) -> dict[str, dict]:
    payload = _request_exact_lookup_json(base, batch_lookup_url(base, chunk))
    found: dict[str, dict] = {}
    for item_id in chunk:
        item = exact_item_from_product_lookup_payload(payload, item_id)
        if isinstance(item, dict):
            found[item_id] = item
    return found


async def prefetch_exact_lookup_items(
    provider: Any,
    item_ids: Iterable[Any],
    *,
    timeout_seconds: float = 30.0,
) -> ExactBatchPrefetchResult:
    ids = _clean_ids(item_ids)
    if len(ids) < PREFETCH_MIN_IDS:
        return ExactBatchPrefetchResult(requested_ids=len(ids))
    base = lookup_base_provider(provider)
    if base is None:
        return ExactBatchPrefetchResult(requested_ids=len(ids))

    calls = cached = failed = 0
    for start in range(0, len(ids), BATCH_LOOKUP_MAX_IDS):
        chunk = ids[start:start + BATCH_LOOKUP_MAX_IDS]
        calls += 1
        try:
            async with walmart_request_slot(priority=EXACT_PRIORITY):
                found = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_chunk_sync, base, chunk),
                    timeout=max(1.0, float(timeout_seconds)),
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - per-item path still runs.
            failed += 1
            log.info(
                "walmart_exact_batch_prefetch failed ids=%s kind=%s error=%s",
                len(chunk),
                str(getattr(error, "retry_kind", "") or type(error).__name__),
                str(error)[:200],
            )
            continue
        cached += store_prefetched_items(base, found)

    log.debug(
        "walmart_exact_batch_prefetch ids=%s calls=%s cached=%s failed=%s",
        len(ids), calls, cached, failed,
    )

    return ExactBatchPrefetchResult(
        requested_ids=len(ids),
        api_calls=calls,
        items_cached=cached,
        failed_calls=failed,
    )
