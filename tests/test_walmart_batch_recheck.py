from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sniperplug.cogs.active_deal_recheck import (
    BATCH_RECHECK_CONCURRENCY,
    BATCH_RECHECK_MAX_ITEMS,
    recheck_walmart_batch,
)


SOURCE = Path("sniperplug/cogs/active_deal_recheck.py").read_text()


@dataclass
class FakeCandidate:
    product_id: str
    current_price: float
    stock_status: str = "In stock"
    can_add_to_cart: bool = True
    seller_name: str = "Walmart.com"
    variant_label: str = "Exact option"


class FakeInner:
    def _candidate_from_item(self, payload, request):
        return FakeCandidate(product_id=str(payload["itemId"]), current_price=float(payload["price"]))


class TrackingProvider:
    def __init__(self, delay: float = 0.01):
        self.inner = FakeInner()
        self.delay = delay
        self.running = 0
        self.max_running = 0

    async def fetch_product_detail_payload(self, item_id):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.delay)
            return {"itemId": item_id, "price": 10.0}
        finally:
            self.running -= 1


def row(item_id: int) -> dict:
    return {
        "active_key": f"walmart:{item_id}",
        "title": f"Item {item_id}",
        "url": f"https://www.walmart.com/ip/Test/{item_id}",
        "current_price": 10.0,
    }


def test_batch_limits_are_hard_coded_and_owner_triggered():
    assert BATCH_RECHECK_MAX_ITEMS == 10
    assert BATCH_RECHECK_CONCURRENCY == 2
    assert 'name="active_deals_recheck"' in SOURCE
    assert "has_permissions(manage_guild=True)" in SOURCE
    assert "app_commands.Range[int, 1, BATCH_RECHECK_MAX_ITEMS]" in SOURCE


def test_batch_never_exceeds_concurrency_limit():
    provider = TrackingProvider()
    results = asyncio.run(
        recheck_walmart_batch(
            provider,
            [row(100000 + index) for index in range(6)],
            concurrency=2,
            timeout_seconds=2,
        )
    )
    assert len(results) == 6
    assert provider.max_running == 2
    assert all(result.status == "unchanged" for _, result in results)


def test_timeout_is_isolated_and_does_not_cancel_other_rows():
    provider = TrackingProvider(delay=0.05)
    results = asyncio.run(
        recheck_walmart_batch(
            provider,
            [row(200001), row(200002), row(200003)],
            concurrency=2,
            timeout_seconds=0,
        )
    )
    assert len(results) == 3
    assert all(result.status == "timeout" for _, result in results)
    assert "cached row was left unchanged" in results[0][1].message


def test_batch_query_only_selects_active_walmart_rows():
    assert "retailer = 'walmart' AND status = 'active'" in SOURCE
    assert "ORDER BY last_seen_at DESC" in SOURCE
    assert "LIMIT ?" in SOURCE


def test_non_truthful_results_are_not_persisted():
    assert 'result.status not in _NON_PERSISTED_STATUSES and result.status != "timeout"' in SOURCE
    assert '"error", "identity_missing", "provider_unsupported"' in SOURCE
    assert "never use SerpApi" in SOURCE
