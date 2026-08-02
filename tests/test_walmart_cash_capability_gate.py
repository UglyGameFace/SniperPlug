from __future__ import annotations

import pytest

from sniperplug.services.walmart_cash_offers import (
    WALMART_CASH_OFFICIAL_CATALOG_URL,
    build_walmart_cash_summary_embed,
    walmart_cash_search_terms,
)
from sniperplug.services.walmart_cash_pipeline import run_walmart_cash_discovery


class FakeConfig:
    enabled = True
    consumer_id = "affiliate-consumer"
    private_key_b64 = "not-used-by-this-test"
    timeout_seconds = 12


class ProductApiOnlyProvider:
    config = FakeConfig()

    def __init__(self) -> None:
        self.scan_calls = 0
        self.detail_calls = 0

    async def scan(self, request):
        self.scan_calls += 1
        raise AssertionError("ordinary product search must not run for Walmart Cash")

    async def fetch_product_detail_payload(self, item_id: str):
        self.detail_calls += 1
        raise AssertionError("ordinary product detail must not run for Walmart Cash")


def test_cash_terms_are_disabled_without_supported_offer_feed() -> None:
    assert walmart_cash_search_terms("walmart cash offers") == ()
    assert walmart_cash_search_terms("tide walmart cash") == ()
    assert walmart_cash_search_terms("personal care") == ()


@pytest.mark.asyncio
async def test_cash_discovery_makes_zero_product_api_calls() -> None:
    provider = ProductApiOnlyProvider()

    result = await run_walmart_cash_discovery(
        provider,
        search="walmart cash offers",
        max_results=8,
        requested_by="test-user",
    )

    assert provider.scan_calls == 0
    assert provider.detail_calls == 0
    assert result.used_queries == ()
    assert result.search_rows_checked == 0
    assert result.detail_rows_attempted == 0
    assert result.detail_rows_checked == 0
    assert result.cash_candidates == ()


def test_cash_summary_routes_to_real_official_catalog() -> None:
    embed = build_walmart_cash_summary_embed(
        "walmart cash offers",
        (),
        checked=0,
        found=0,
        warnings=(),
        detail_checked=0,
        promo_counts={},
    )
    rendered = str(embed.to_dict())

    assert "not a supported Walmart Cash offer feed" in rendered
    assert "Product searches made" in rendered
    assert "Fake no-offer conclusion" in rendered
    assert WALMART_CASH_OFFICIAL_CATALOG_URL in rendered
    assert "Products searched: **0**" not in rendered
