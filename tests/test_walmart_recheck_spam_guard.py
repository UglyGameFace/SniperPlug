from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sniperplug.services.walmart_deal_recheck import recheck_walmart_observation
from sniperplug.services.walmart_recheck_guard import (
    WALMART_RECHECK_CACHE_MAX_ITEMS,
    WALMART_RECHECK_ERROR_REUSE_SECONDS,
    WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS,
    WALMART_RECHECK_REUSE_SECONDS,
    clear_walmart_recheck_guard,
)


GUARD_SOURCE = Path("sniperplug/services/walmart_recheck_guard.py").read_text()
SERVICE_SOURCE = Path("sniperplug/services/walmart_deal_recheck.py").read_text()


@dataclass
class FakeCandidate:
    product_id: str
    current_price: float = 10.0
    api_reference_price: float = 20.0
    stock_status: str = "In stock"
    can_add_to_cart: bool = True


class FakeInner:
    def _candidate_from_item(self, payload, request):
        return FakeCandidate(product_id=str(payload["itemId"]))


class CountingProvider:
    def __init__(self, delay: float = 0.02):
        self.inner = FakeInner()
        self.delay = delay
        self.calls = 0

    async def fetch_product_detail_payload(self, item_id):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {"itemId": item_id}


def row(item_id: int) -> dict:
    return {
        "active_key": f"walmart:{item_id}",
        "url": f"https://www.walmart.com/ip/Test/{item_id}",
        "current_price": 10.0,
        "discount": 50.0,
        "source_label": "test",
        "title": f"Item {item_id}",
    }


def test_concurrent_same_item_rechecks_collapse_to_one_provider_call():
    async def scenario():
        await clear_walmart_recheck_guard()
        provider = CountingProvider()
        results = await asyncio.gather(
            recheck_walmart_observation(provider, row(700001)),
            recheck_walmart_observation(provider, row(700001)),
            recheck_walmart_observation(provider, row(700001)),
        )
        assert provider.calls == 1
        assert all(result.status == "unchanged" for result in results)
        assert sum(bool(result.reused) for result in results) == 2

    asyncio.run(scenario())


def test_sequential_repeat_reuses_recent_exact_item_result():
    async def scenario():
        await clear_walmart_recheck_guard()
        provider = CountingProvider(delay=0)
        first = await recheck_walmart_observation(provider, row(700002))
        second = await recheck_walmart_observation(provider, row(700002))
        assert provider.calls == 1
        assert first.reused is False
        assert second.reused is True
        assert "avoid another provider call" in second.message

    asyncio.run(scenario())


def test_different_item_ids_do_not_share_results():
    async def scenario():
        await clear_walmart_recheck_guard()
        provider = CountingProvider(delay=0)
        await recheck_walmart_observation(provider, row(700003))
        await recheck_walmart_observation(provider, row(700004))
        assert provider.calls == 2

    asyncio.run(scenario())


def test_guard_has_hard_reuse_timeout_and_memory_bounds():
    assert WALMART_RECHECK_REUSE_SECONDS == 60
    assert WALMART_RECHECK_ERROR_REUSE_SECONDS == 8
    assert WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS == 25
    assert WALMART_RECHECK_CACHE_MAX_ITEMS == 512
    assert "asyncio.wait_for" in GUARD_SOURCE
    assert "while len(_recent_results) > WALMART_RECHECK_CACHE_MAX_ITEMS" in GUARD_SOURCE


def test_every_existing_recheck_entry_point_uses_the_guarded_service_function():
    assert "guarded_walmart_recheck" in SERVICE_SOURCE
    assert "async def _perform_walmart_recheck" in SERVICE_SOURCE
    assert "async def recheck_walmart_observation" in SERVICE_SOURCE
    assert "reused: bool = False" in SERVICE_SOURCE
