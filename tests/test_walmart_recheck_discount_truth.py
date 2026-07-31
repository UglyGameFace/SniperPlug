from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sniperplug.services.walmart_deal_recheck import calculate_discount, recheck_walmart_observation


@dataclass
class FakeCandidate:
    product_id: str = "123456789"
    current_price: float | None = 50.0
    api_reference_price: float | None = 100.0
    typical_price: float | None = None
    deal_lane: str | None = None
    stock_status: str = "In stock"
    can_add_to_cart: bool | None = True
    seller_name: str = "Walmart.com"
    variant_attributes: dict[str, str] = field(default_factory=dict)


class FakeInner:
    def __init__(self, candidate):
        self.candidate = candidate

    def _candidate_from_item(self, payload, request):
        return self.candidate


class FakeProvider:
    def __init__(self, candidate):
        self.inner = FakeInner(candidate)

    async def fetch_product_detail_payload(self, item_id):
        return {"itemId": item_id}


def row(*, price=50.0, discount=50.0, source_label="walmart"):
    return {
        "active_key": "walmart:123456789",
        "title": "Test item",
        "url": "https://www.walmart.com/ip/Test/123456789",
        "current_price": price,
        "discount": discount,
        "source_label": source_label,
    }


def run(candidate, cached=None):
    return asyncio.run(recheck_walmart_observation(FakeProvider(candidate), cached or row()))


def test_calculate_discount_uses_current_reference_pair_only():
    assert calculate_discount(50, 100) == 50.0
    assert calculate_discount(75, 100) == 25.0
    assert calculate_discount(100, 100) == 0.0
    assert calculate_discount(110, 100) == 0.0
    assert calculate_discount(50, None) is None


def test_recheck_classifies_improved_and_weakened_markdowns():
    improved = run(FakeCandidate(current_price=40, api_reference_price=100))
    assert improved.status == "deal_improved"
    assert improved.current_discount == 60.0
    assert improved.cache_status == "active"

    weakened = run(FakeCandidate(current_price=75, api_reference_price=100))
    assert weakened.status == "deal_weakened"
    assert weakened.current_discount == 25.0
    assert weakened.cache_status == "active"


def test_recheck_removes_row_when_verified_discount_is_gone():
    result = run(FakeCandidate(current_price=100, api_reference_price=100))
    assert result.status == "discount_gone"
    assert result.current_discount == 0.0
    assert result.cache_status == "stale"


def test_missing_reference_clears_old_markdown_and_stales_normal_deal():
    result = run(FakeCandidate(current_price=50, api_reference_price=None))
    assert result.status == "discount_unproven"
    assert result.current_discount is None
    assert result.cache_status == "stale"
    assert "cleared that claim" in result.message


def test_explicit_walmart_cash_survives_without_fake_markdown():
    candidate = FakeCandidate(
        current_price=50,
        api_reference_price=None,
        deal_lane="walmart_cash",
        variant_attributes={"walmartCashSavings": "10"},
    )
    result = run(candidate, row(source_label="walmart_cash"))
    assert result.status == "promotion_verified"
    assert result.current_discount is None
    assert result.cache_status == "active"


def test_existing_rows_without_discount_are_not_killed_for_missing_reference():
    result = run(
        FakeCandidate(current_price=45, api_reference_price=None),
        row(price=50, discount=None),
    )
    assert result.status == "price_changed"
    assert result.current_discount is None
    assert result.cache_status == "active"
