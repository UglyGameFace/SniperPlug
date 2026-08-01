from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_price_enrichment import enrich_walmart_exact_prices


class FakeDetailProvider:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.calls: list[str] = []
        self.inner = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.calls.append(item_id)
        return self.payloads[item_id]


def candidate(
    item_id: str,
    *,
    price: float = 20.0,
    attrs: dict[str, str] | None = None,
    signals: list[str] | None = None,
) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Search item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=price,
        api_current_price=price,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes=dict(attrs or {}),
        signals=list(signals or []),
    )


def test_exact_detail_refreshes_same_item_current_and_was_price() -> None:
    original = candidate(
        "123456",
        attrs={"finderSourceQuery": "monitor clearance", "clearance": "yes"},
        signals=["clearance"],
    )
    provider = FakeDetailProvider(
        {
            "123456": {
                "itemId": 123456,
                "name": "Exact Gaming Monitor",
                "salePrice": 79.99,
                "wasPrice": 149.99,
                "sellerName": "Walmart",
                "availableOnline": True,
                "stock": "Available",
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    exact = result.candidates[0]
    assert provider.calls == ["123456"]
    assert result.enriched == 1
    assert result.references_found == 1
    assert exact.current_price == 79.99
    assert exact.api_current_price == 79.99
    assert exact.typical_price == 149.99
    assert exact.api_reference_price == 149.99
    assert exact.api_reference_path == "wasPrice"
    assert exact.variant_attributes["exactDetailPriceProof"] == "yes"
    assert exact.variant_attributes["exactDetailItemId"] == "123456"
    assert exact.variant_attributes["exactDetailReferenceSource"] == "wasPrice"
    assert exact.variant_attributes["finderSourceQuery"] == "monitor clearance"
    assert exact.candidate_id == original.candidate_id
    assert "exact Walmart detail item verified: 123456" in exact.signals


def test_exact_detail_identity_mismatch_never_overwrites_candidate() -> None:
    original = candidate("222222", attrs={"rollback": "yes"}, signals=["rollback"])
    provider = FakeDetailProvider(
        {
            "222222": {
                "itemId": 999999,
                "name": "Wrong returned item",
                "salePrice": 5.0,
                "wasPrice": 100.0,
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    assert result.enriched == 0
    assert result.identity_mismatches == 1
    assert result.candidates[0] is original
    assert result.candidates[0].typical_price is None


def test_exact_detail_without_was_price_stays_honest() -> None:
    original = candidate("333333", attrs={"clearance": "yes"}, signals=["clearance"])
    provider = FakeDetailProvider(
        {
            "333333": {
                "itemId": 333333,
                "name": "Exact clearance item",
                "salePrice": 14.0,
                "availableOnline": True,
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    exact = result.candidates[0]
    assert result.enriched == 1
    assert result.references_found == 0
    assert exact.current_price == 14.0
    assert exact.typical_price is None
    assert exact.api_reference_price is None
    assert exact.variant_attributes["exactDetailPriceProof"] == "yes"
    assert "exactDetailReferenceSource" not in exact.variant_attributes


def test_bounded_enrichment_prioritizes_missing_clearance_was_price() -> None:
    ordinary = candidate("444444")
    clearance = candidate(
        "555555",
        attrs={"finderSourceQuery": "toy clearance", "clearance": "yes"},
        signals=["clearance"],
    )
    provider = FakeDetailProvider(
        {
            "444444": {
                "itemId": 444444,
                "name": "Ordinary item",
                "salePrice": 20.0,
                "wasPrice": 25.0,
            },
            "555555": {
                "itemId": 555555,
                "name": "Clearance item",
                "salePrice": 10.0,
                "wasPrice": 50.0,
            },
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices(
            [ordinary, clearance],
            provider=provider,
            limit=1,
        )
    )

    assert provider.calls == ["555555"]
    assert result.attempted == 1
    assert result.skipped == 1
    assert result.candidates[0] is ordinary
    assert result.candidates[1].typical_price == 50.0


def test_autoscan_enriches_before_cards_and_price_memory() -> None:
    source = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(
        encoding="utf-8"
    )

    enrich_pos = source.index("exact_prices = await enrich_walmart_exact_prices")
    aggregate_pos = source.index("aggregate = ProviderScanResult")
    review_pos = source.index("review_candidates = build_review_candidate_cards")
    memory_pos = source.index("select_observed_price_drop_cards")

    assert "provider_registry.get(\"walmart\")" in source
    assert "AUTOSCAN_EXACT_DETAIL_LIMIT = 8" in source
    assert enrich_pos < aggregate_pos < review_pos < memory_pos
