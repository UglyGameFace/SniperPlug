from __future__ import annotations

from dataclasses import dataclass

import pytest

from sniperplug.services.walmart_deal_recheck import extract_walmart_item_id, recheck_walmart_observation


@dataclass
class FakeCandidate:
    product_id: str
    current_price: float | None
    stock_status: str = "In stock"
    can_add_to_cart: bool | None = True
    seller_name: str | None = "Walmart.com"
    variant_label: str | None = "Black"


class FakeInner:
    def __init__(self, candidate):
        self.candidate = candidate

    def _candidate_from_item(self, payload, request):
        return self.candidate


class FakeProvider:
    def __init__(self, candidate, payload=None):
        self.inner = FakeInner(candidate)
        self.payload = payload or {"itemId": "123456789"}
        self.requested_item_id = None

    async def fetch_product_detail_payload(self, item_id):
        self.requested_item_id = item_id
        return self.payload


def row(price=20.0):
    return {
        "active_key": "walmart:123456789",
        "url": "https://www.walmart.com/ip/Test-Item/123456789",
        "current_price": price,
    }


def test_extract_walmart_item_id_never_uses_title_guessing():
    assert extract_walmart_item_id("https://www.walmart.com/ip/Test-Item/123456789") == "123456789"
    assert extract_walmart_item_id("https://www.walmart.com/ip/123456789?athbdg=L1100") == "123456789"
    assert extract_walmart_item_id("https://example.com/no-id", "walmart:offer:987654321") == "987654321"
    assert extract_walmart_item_id("https://example.com/no-id", "walmart:headphones") is None


@pytest.mark.asyncio
async def test_recheck_reports_unchanged_exact_item():
    provider = FakeProvider(FakeCandidate(product_id="123456789", current_price=20.0))
    result = await recheck_walmart_observation(provider, row())
    assert provider.requested_item_id == "123456789"
    assert result.status == "unchanged"
    assert result.cache_status == "active"


@pytest.mark.asyncio
async def test_recheck_reports_price_change():
    provider = FakeProvider(FakeCandidate(product_id="123456789", current_price=15.0))
    result = await recheck_walmart_observation(provider, row())
    assert result.status == "price_changed"
    assert result.old_price == 20.0
    assert result.current_price == 15.0


@pytest.mark.asyncio
async def test_recheck_blocks_identity_mismatch():
    provider = FakeProvider(FakeCandidate(product_id="999999999", current_price=15.0))
    result = await recheck_walmart_observation(provider, row())
    assert result.status == "identity_mismatch"
    assert result.cache_status == "stale"
    assert "refused to overwrite" in result.message


@pytest.mark.asyncio
async def test_recheck_marks_unavailable_exact_item_stale():
    provider = FakeProvider(
        FakeCandidate(
            product_id="123456789",
            current_price=20.0,
            stock_status="Out of stock",
            can_add_to_cart=False,
        )
    )
    result = await recheck_walmart_observation(provider, row())
    assert result.status == "unavailable"
    assert result.cache_status == "stale"
