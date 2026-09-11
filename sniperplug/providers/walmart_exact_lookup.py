from __future__ import annotations

import asyncio
import gzip
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from sniperplug.providers.walmart import WalmartProviderError, _trusted_current_price


AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE = "affiliate_product_lookup_exact_item"
AFFILIATE_PRODUCT_LOOKUP_ROUTE = "items_ids_v274"
EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER = "lookup_shape_v275"
EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER = "lookup_http_error_decode_v286"

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
_PRICE_DIAGNOSTIC_PRICE_CHARS = 150
_PRICE_DIAGNOSTIC_OFFER_CHARS = 80
_PRICE_DIAGNOSTIC_KEY_CHARS = 60
_PRICE_DIAGNOSTIC_MAX_CHARS = 320
_PRICE_DIAGNOSTIC_INTERVAL_SECONDS = 60.0
_HTTP_400_DIAGNOSTIC_INTERVAL_SECONDS = 300.0
_PRICE_DIAGNOSTIC_NEXT_ATTR = "_sniperplug_exact_lookup_price_diag_next_at"
_HTTP_400_DIAGNOSTIC_NEXT_ATTR = "_sniperplug_exact_lookup_http400_diag_next_at"
_DIAGNOSTIC_RESERVATION_LOCK = threading.Lock()

log = logging.getLogger("sniperplug.autoscan.walmart_exact_lookup")


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
    signed_headers = getattr(provider, "_signed_headers", None)
    if not detail_url or (not callable(request_json) and not callable(signed_headers)):
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
    try:
        payload = _request_exact_lookup_json(provider, url)
    except WalmartProviderError as error:
        if str(getattr(error, "retry_kind", "") or "").strip().lower() == "api_http_400":
            _maybe_log_http_400(provider, clean_id, error)
        raise

    item = exact_item_from_product_lookup_payload(payload, clean_id)
    if item is None:
        raise WalmartProviderError(
            f"Walmart Affiliate Product Lookup returned no exact item match for {clean_id}.",
            retry_kind="api_exact_lookup_no_match",
        )

    exact = dict(item)
    exact["sniperplugOfficialExactSource"] = AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE
    exact["sniperplugOfficialExactRoute"] = AFFILIATE_PRODUCT_LOOKUP_ROUTE

    current_price, _current_signal = _trusted_current_price(exact)
    if current_price is None or current_price <= 0:
        _maybe_log_missing_price_shape(provider, clean_id, exact)
    return exact


def _request_exact_lookup_json(provider: Any, url: str) -> dict:
    """Request exact Product Lookup with readable, closed HTTP-error bodies.

    Walmart's legacy Affiliate Product Lookup can return a gzip-compressed error
    entity on HTTP 400. The provider's historical generic error path decodes the
    compressed bytes directly as UTF-8, which turns the only useful upstream
    validation detail into binary-looking garbage and can leave the HTTPError's
    socket wrapper open. Keep endpoint and signature semantics unchanged here,
    but decode Content-Encoding before surfacing the bounded error text and close
    the error response deterministically.

    Lightweight test doubles that only expose ``_request_json`` keep using that
    method; the real Walmart provider exposes ``_signed_headers`` and therefore
    uses this exact-lookup transport path in production.
    """

    signed_headers = getattr(provider, "_signed_headers", None)
    if not callable(signed_headers):
        request_json = getattr(provider, "_request_json", None)
        if not callable(request_json):
            raise WalmartProviderError(
                "Walmart Affiliate Product Lookup request transport is unavailable.",
                retry_kind="api_exact_lookup_unavailable",
            )
        return request_json(url)

    config = getattr(provider, "config", None)
    timeout_seconds = max(1, int(getattr(config, "timeout_seconds", 12) or 12))
    request = urllib.request.Request(
        url,
        headers=signed_headers(),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            encoded_body = exc.read()
            content_encoding = str(
                getattr(exc, "headers", {}).get("Content-Encoding", "") or ""
            )
        finally:
            exc.close()
        body = _decode_http_error_body(
            encoded_body,
            content_encoding=content_encoding,
        )[:400]
        raise WalmartProviderError(
            f"Walmart API HTTP {exc.code}: {body}",
            retry_kind=f"api_http_{int(exc.code)}",
        ) from exc
    except TimeoutError as exc:
        raise WalmartProviderError(
            "Walmart API request timed out.",
            retry_kind="api_timeout",
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        retry_kind = "api_timeout" if isinstance(reason, TimeoutError) else "api_network"
        raise WalmartProviderError(
            f"Walmart API network error: {reason}",
            retry_kind=retry_kind,
        ) from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WalmartProviderError(
            "Walmart API returned non-JSON response.",
            retry_kind="api_non_json",
        ) from exc
    if not isinstance(decoded, dict):
        raise WalmartProviderError(
            "Walmart API returned unexpected payload shape.",
            retry_kind="api_payload_shape",
        )
    return decoded


def _decode_http_error_body(raw: bytes, *, content_encoding: str) -> str:
    data = bytes(raw or b"")
    encodings = [
        token.strip().lower()
        for token in str(content_encoding or "").split(",")
        if token.strip()
    ]
    try:
        for encoding in reversed(encodings):
            if encoding in {"", "identity"}:
                continue
            if encoding == "gzip":
                data = gzip.decompress(data)
                continue
            if encoding == "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
                continue
            break
    except (OSError, EOFError, zlib.error):
        data = bytes(raw or b"")
    return data.decode("utf-8", errors="replace")


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
    stack: list[tuple[Any, str, int, bool]] = [(item, "", 0, False)]
    visited = 0

    while stack and visited < _PRICE_DIAGNOSTIC_MAX_NODES:
        node, path, depth, parent_price_context = stack.pop()
        visited += 1
        if depth > _PRICE_DIAGNOSTIC_MAX_DEPTH:
            continue

        if isinstance(node, dict):
            for raw_key, child in reversed(list(node.items())):
                key = str(raw_key or "")
                clean_key = _diagnostic_token(key)
                child_path = f"{path}.{clean_key}" if path else clean_key
                lowered = key.strip().lower()
                key_is_price_like = any(
                    fragment in lowered for fragment in _PRICE_DIAGNOSTIC_KEY_FRAGMENTS
                )

                if len(price_fields) < _PRICE_DIAGNOSTIC_MAX_FIELDS:
                    if key_is_price_like:
                        price_fields.append(
                            f"{child_path}:{_diagnostic_value(child)}"
                        )
                    elif parent_price_context and _diagnostic_scalar_candidate(child):
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
                    stack.append(
                        (
                            child,
                            child_path,
                            depth + 1,
                            parent_price_context or key_is_price_like,
                        )
                    )
            continue

        if isinstance(node, list):
            for index in range(len(node) - 1, -1, -1):
                child = node[index]
                if isinstance(child, (dict, list)):
                    child_path = f"{path}[{index}]" if path else f"[{index}]"
                    stack.append(
                        (child, child_path, depth + 1, parent_price_context)
                    )

    prices = _bounded_diagnostic_join(
        price_fields,
        delimiter="|",
        max_chars=_PRICE_DIAGNOSTIC_PRICE_CHARS,
        empty="none",
    )
    offers = _bounded_diagnostic_join(
        offer_shapes,
        delimiter="|",
        max_chars=_PRICE_DIAGNOSTIC_OFFER_CHARS,
        empty="none",
    )
    keys = _bounded_diagnostic_join(
        [value for value in item_keys if value],
        delimiter=",",
        max_chars=_PRICE_DIAGNOSTIC_KEY_CHARS,
        empty="none",
    )
    summary = f"prices={prices};offers={offers};keys={keys}"
    return summary[:_PRICE_DIAGNOSTIC_MAX_CHARS].rstrip(";|,")


def _maybe_log_missing_price_shape(provider: Any, item_id: str, payload: Any) -> None:
    if not _reserve_diagnostic_slot(
        provider,
        attr_name=_PRICE_DIAGNOSTIC_NEXT_ATTR,
        interval_seconds=_PRICE_DIAGNOSTIC_INTERVAL_SECONDS,
    ):
        return
    log.info(
        "Walmart exact Product Lookup missing-current-price diagnostic "
        "marker=%s item_id=%s route=%s shape=%s",
        EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER,
        item_id,
        AFFILIATE_PRODUCT_LOOKUP_ROUTE,
        summarize_product_lookup_price_shape(payload, item_id),
    )


def _maybe_log_http_400(provider: Any, item_id: str, error: Exception) -> None:
    if not _reserve_diagnostic_slot(
        provider,
        attr_name=_HTTP_400_DIAGNOSTIC_NEXT_ATTR,
        interval_seconds=_HTTP_400_DIAGNOSTIC_INTERVAL_SECONDS,
    ):
        return
    log.info(
        "Walmart exact Product Lookup HTTP 400 diagnostic marker=%s decode=%s item_id=%s detail=%s",
        EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER,
        EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER,
        item_id,
        _diagnostic_token(error, max_chars=220),
    )


def _reserve_diagnostic_slot(
    provider: Any,
    *,
    attr_name: str,
    interval_seconds: float,
    now_monotonic: float | None = None,
) -> bool:
    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _DIAGNOSTIC_RESERVATION_LOCK:
        next_at = float(getattr(provider, attr_name, 0.0) or 0.0)
        if next_at > now_value:
            return False
        setattr(provider, attr_name, now_value + max(1.0, float(interval_seconds)))
    return True


def _bounded_diagnostic_join(
    values: list[str],
    *,
    delimiter: str,
    max_chars: int,
    empty: str,
) -> str:
    if not values:
        return empty
    return delimiter.join(values)[:max(1, int(max_chars))].rstrip(delimiter)


def _diagnostic_scalar_candidate(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        compact = "".join(value.split())
        return bool(compact and len(compact) <= 36 and any(char.isdigit() for char in compact))
    return False


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
    "EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER",
    "EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER",
    "exact_item_from_product_lookup_payload",
    "fetch_exact_affiliate_product_lookup_payload",
    "summarize_product_lookup_price_shape",
]
