from __future__ import annotations

import asyncio
import threading
import time

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import (
    DealProvider,
    ProviderCapability,
    ProviderHealth,
    ProviderScanRequest,
    ProviderScanResult,
    ProviderStatus,
)
from sniperplug.providers.cached_walmart import CachedWalmartProvider
from sniperplug.providers.coordinated_walmart import CoordinatedWalmartProvider
from sniperplug.providers.registry import ProviderRegistry
from sniperplug.services.walmart_request_coordinator import (
    EXACT_PRIORITY,
    SEARCH_PRIORITY,
    WalmartRequestCoordinator,
)


class FakeWalmartProvider(DealProvider):
    provider_key = "walmart"
    display_name = "Walmart"
    capabilities = frozenset({ProviderCapability.PRODUCT_LOOKUP})

    def __init__(self):
        self.inner = self
        self.scan_calls = 0
        self.detail_calls = 0

    async def healthcheck(self):
        return ProviderHealth(
            provider_key="walmart",
            ok=True,
            status=ProviderStatus.READY,
            message="ready",
        )

    async def scan(self, request):
        self.scan_calls += 1
        return ProviderScanResult(provider_key="walmart", candidates=())

    async def fetch_product_detail_payload(self, item_id):
        self.detail_calls += 1
        return {"itemId": item_id}

    def _candidate_from_item(self, item, *, request):
        return SourceCandidate(
            source_key="walmart",
            retailer="Walmart",
            title="test",
            product_url="https://www.walmart.com/ip/1",
        )


def test_exact_waiter_gets_next_slot_before_search_waiter() -> None:
    coordinator = WalmartRequestCoordinator(capacity=1)
    initial = coordinator.acquire(priority=SEARCH_PRIORITY)
    assert initial.priority == SEARCH_PRIORITY
    order: list[str] = []

    def wait(label: str, priority: int) -> None:
        coordinator.acquire(priority=priority, timeout_seconds=3)
        try:
            order.append(label)
            time.sleep(0.02)
        finally:
            coordinator.release()

    search = threading.Thread(target=wait, args=("search", SEARCH_PRIORITY))
    exact = threading.Thread(target=wait, args=("exact", EXACT_PRIORITY))
    search.start()
    time.sleep(0.03)
    exact.start()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = coordinator.snapshot()
        if snapshot["waiting_search"] == 1 and snapshot["waiting_exact"] == 1:
            break
        time.sleep(0.01)

    coordinator.release()
    search.join(timeout=3)
    exact.join(timeout=3)

    assert order == ["exact", "search"]


def test_registry_preserves_generic_provider_identity() -> None:
    registry = ProviderRegistry()
    generic = FakeWalmartProvider()

    registry.register(generic)

    assert registry.get("WALMART") is generic


def test_registry_wraps_real_cached_walmart_once_and_preserves_exact_builder() -> None:
    registry = ProviderRegistry()
    raw = FakeWalmartProvider()
    cached = CachedWalmartProvider(db=object(), inner=raw)

    registry.register(cached)
    registered = registry.get("walmart")

    assert isinstance(registered, CoordinatedWalmartProvider)
    assert registered.delegate is cached
    assert registered.inner is raw
    assert callable(registered.inner._candidate_from_item)

    registry.register(registered, replace=True)
    assert registry.get("walmart") is registered


def test_coordinated_provider_delegates_scan_and_detail() -> None:
    async def run() -> None:
        raw = FakeWalmartProvider()
        provider = CoordinatedWalmartProvider(raw)

        result = await provider.scan(
            ProviderScanRequest(source_key="test", query="laptop")
        )
        payload = await provider.fetch_product_detail_payload("123")

        assert result.provider_key == "walmart"
        assert payload == {"itemId": "123"}
        assert raw.scan_calls == 1
        assert raw.detail_calls == 1

    asyncio.run(run())
