from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import pytest

from sniperplug.providers import walmart_lookup_batch_cache as cache
from sniperplug.providers.cached_walmart import CachedWalmartProvider
from sniperplug.providers.coordinated_walmart import CoordinatedWalmartProvider
from sniperplug.providers.walmart import (
    WalmartAffiliateConfig,
    WalmartProvider,
    WalmartProviderError,
)
from sniperplug.providers.walmart_exact_lookup import (
    AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE,
    _fetch_exact_affiliate_product_lookup_payload_sync,
)
from sniperplug.services import walmart_exact_batch_prefetch as prefetch


def provider() -> WalmartProvider:
    return WalmartProvider(
        WalmartAffiliateConfig(enabled=True, consumer_id="test", private_key_b64="test")
    )


def item(item_id: str, price: float = 1.0) -> dict:
    return {"itemId": int(item_id), "name": f"Item {item_id}", "salePrice": price}


def ids_from_url(url: str) -> list[str]:
    return parse_qs(urlparse(url).query)["ids"][0].split(",")


def test_cache_is_exact_id_single_use_and_expires(monkeypatch) -> None:
    base = provider()
    cache.store_prefetched_items(base, {"111": item("111"), "222": item("999")})
    first = cache.take_prefetched_item(base, "111")
    assert first is not None and first["itemId"] == 111
    assert cache.take_prefetched_item(base, "111") is None  # single use
    assert cache.take_prefetched_item(base, "222") is None  # returned ID mismatch

    cache.store_prefetched_items(base, {"333": item("333")})
    now = time.monotonic()
    monkeypatch.setattr(cache.time, "monotonic", lambda: now + cache.BATCH_CACHE_TTL_SECONDS + 1)
    assert cache.take_prefetched_item(base, "333") is None


def test_prefetch_uses_one_call_per_twenty_ids(monkeypatch) -> None:
    base = provider()
    urls: list[str] = []

    def fake_request(provider_arg, url):
        assert provider_arg is base
        urls.append(url)
        return {"items": [item(i) for i in ids_from_url(url)]}

    monkeypatch.setattr(prefetch, "_request_exact_lookup_json", fake_request)
    ids = [str(100000 + n) for n in range(45)] + ["100000", "not-a-number", ""]
    result = asyncio.run(prefetch.prefetch_exact_lookup_items(base, ids))

    assert [len(ids_from_url(url)) for url in urls] == [20, 20, 5]
    assert result.api_calls == 3 and result.items_cached == 45 and result.failed_calls == 0
    assert all(parse_qs(urlparse(url).query)["responseGroup"] == ["full"] for url in urls)
    assert urls[0].startswith(WalmartProvider.detail_url + "?ids=100000,100001,")
    cache.clear_prefetched_items(base)


def test_partial_batch_caches_only_returned_items(monkeypatch) -> None:
    base = provider()
    monkeypatch.setattr(
        prefetch, "_request_exact_lookup_json",
        lambda _p, url: {"items": [item("201")]},
    )
    result = asyncio.run(prefetch.prefetch_exact_lookup_items(base, ["201", "202"]))
    assert result.items_cached == 1
    assert cache.take_prefetched_item(base, "202") is None
    assert cache.take_prefetched_item(base, "201") is not None


def test_failed_batch_caches_nothing_and_does_not_raise(monkeypatch) -> None:
    base = provider()

    def boom(_p, _url):
        raise WalmartProviderError("Walmart API HTTP 429: slow down", retry_kind="api_http_429")

    monkeypatch.setattr(prefetch, "_request_exact_lookup_json", boom)
    result = asyncio.run(prefetch.prefetch_exact_lookup_items(base, ["301", "302"]))
    assert result.failed_calls == 1 and result.items_cached == 0
    assert cache.take_prefetched_item(base, "301") is None


def test_single_id_and_non_walmart_providers_skip_prefetch(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        prefetch, "_request_exact_lookup_json",
        lambda _p, url: calls.append(url) or {"items": []},
    )
    asyncio.run(prefetch.prefetch_exact_lookup_items(provider(), ["401"]))

    class Double:
        detail_url = WalmartProvider.detail_url

        def _request_json(self, url):  # pragma: no cover - must not be called
            raise AssertionError("test double must keep its own per-item behavior")

    asyncio.run(prefetch.prefetch_exact_lookup_items(Double(), ["402", "403"]))
    assert calls == []


def test_production_wrappers_resolve_to_the_signing_base_provider() -> None:
    base = provider()
    wrapped = CoordinatedWalmartProvider(CachedWalmartProvider(None, inner=base))
    assert prefetch.lookup_base_provider(wrapped) is base


def test_detail_and_lookup_paths_use_prefetched_item_without_new_request() -> None:
    base = provider()

    def no_network(*_args, **_kwargs):
        raise AssertionError("prefetched item must not trigger another API call")

    base._request_json = no_network
    base._signed_headers = no_network

    cache.store_prefetched_items(base, {"501": item("501", 3.0)})
    detail = base._product_detail_payload("501")
    assert detail["itemId"] == 501 and detail["salePrice"] == 3.0

    cache.store_prefetched_items(base, {"502": item("502", 4.0)})
    exact = _fetch_exact_affiliate_product_lookup_payload_sync(base, "502")
    assert exact["itemId"] == 502
    assert exact["sniperplugOfficialExactSource"] == AFFILIATE_PRODUCT_LOOKUP_EXACT_SOURCE

    # Once consumed, the original per-item request path runs again.
    with pytest.raises(AssertionError, match="must not trigger"):
        base._product_detail_payload("501")
