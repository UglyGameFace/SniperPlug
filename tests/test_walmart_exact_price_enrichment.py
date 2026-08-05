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
    reference: float | None = None,
    attrs: dict[str, str] | None = None,
    signals: list[str] | None = None,
) -> SourceCandidate:
    merged_attrs = dict(attrs or {})
    if reference is not None:
        merged_attrs.setdefault("referencePriceTrusted", "yes")
        merged_attrs.setdefault("trustedReferencePrice", f"{reference:.2f}")
        merged_attrs.setdefault("trustedReferenceSource", "search.wasPrice")
    discount = (
        round((reference - price) / reference * 100, 2)
        if reference is not None and reference > price
        else None
    )
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Search item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=price,
        typical_price=reference,
        api_current_price=price,
        api_reference_price=reference,
        api_discount_percent=discount,
        api_reference_path="search.wasPrice" if reference is not None else None,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes=merged_attrs,
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
    assert result.proofs_blocked == 0
    assert exact.current_price == 79.99
    assert exact.api_current_price == 79.99
    assert exact.typical_price == 149.99
    assert exact.api_reference_price == 149.99
    assert exact.api_reference_path == "item.wasPrice"
    assert exact.variant_attributes["exactDetailPriceProof"] == "yes"
    assert exact.variant_attributes["exactDetailItemId"] == "123456"
    assert exact.variant_attributes["exactDetailReferenceSource"] == "item.wasPrice"
    assert exact.variant_attributes["exactDetailReferenceStatus"] == "trusted"
    assert exact.variant_attributes["referencePriceTrusted"] == "yes"
    assert exact.variant_attributes["finderSourceQuery"] == "monitor clearance"
    assert exact.candidate_id == original.candidate_id
    assert "exact Walmart detail item verified: 123456" in exact.signals


def test_exact_detail_identity_mismatch_blocks_search_reference_proof() -> None:
    original = candidate(
        "222222",
        price=20.0,
        reference=100.0,
        attrs={"rollback": "yes"},
        signals=["rollback"],
    )
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

    blocked = result.candidates[0]
    assert result.enriched == 0
    assert result.identity_mismatches == 1
    assert result.proofs_blocked == 1
    assert blocked is original
    assert blocked.current_price == 20.0
    assert blocked.typical_price is None
    assert blocked.api_reference_price is None
    assert blocked.api_reference_path is None
    assert blocked.api_discount_percent is None
    assert blocked.variant_attributes["referencePriceTrusted"] == "no"
    assert blocked.variant_attributes["exactDetailPriceProof"] == "no"
    assert blocked.variant_attributes["exactDetailReferenceStatus"] == "identity_mismatch"
    assert blocked.variant_attributes["observedPriceFallback"] == "exact_item_baseline"


def test_exact_detail_failure_blocks_search_reference_but_keeps_current_price() -> None:
    original = candidate("232323", price=18.0, reference=90.0)
    provider = FakeDetailProvider({})

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    blocked = result.candidates[0]
    assert provider.calls == ["232323"]
    assert result.failed == 1
    assert result.proofs_blocked == 1
    assert blocked.current_price == 18.0
    assert blocked.api_current_price == 18.0
    assert blocked.typical_price is None
    assert blocked.api_reference_price is None
    assert blocked.api_discount_percent is None
    assert blocked.variant_attributes["exactDetailReferenceStatus"] == "failed"
    assert blocked.variant_attributes["referencePriceTrusted"] == "no"


def test_exact_detail_without_was_price_stays_honest_and_marks_memory_fallback() -> None:
    original = candidate(
        "333333",
        price=20.0,
        reference=99.0,
        attrs={"clearance": "yes"},
        signals=["clearance"],
    )
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
    assert exact.api_discount_percent is None
    assert exact.variant_attributes["exactDetailPriceProof"] == "yes"
    assert exact.variant_attributes["exactDetailReferenceStatus"] == "missing"
    assert exact.variant_attributes["referencePriceTrusted"] == "no"
    assert exact.variant_attributes["observedPriceFallback"] == "exact_item_baseline"
    assert "exactDetailReferenceSource" not in exact.variant_attributes
    assert "trustedReferencePrice" not in exact.variant_attributes
    assert "trustedReferenceSource" not in exact.variant_attributes


def test_bounded_enrichment_prioritizes_public_threshold_reference_proof() -> None:
    missing_clearance = candidate(
        "444444",
        attrs={"finderSourceQuery": "toy clearance", "clearance": "yes"},
        signals=["clearance"],
    )
    public_search_markdown = candidate(
        "555555",
        price=10.0,
        reference=50.0,
    )
    provider = FakeDetailProvider(
        {
            "444444": {
                "itemId": 444444,
                "name": "Clearance item",
                "salePrice": 10.0,
                "wasPrice": 20.0,
            },
            "555555": {
                "itemId": 555555,
                "name": "Public candidate",
                "salePrice": 10.0,
                "wasPrice": 50.0,
            },
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices(
            [missing_clearance, public_search_markdown],
            provider=provider,
            limit=1,
            min_discount=50,
        )
    )

    assert provider.calls == ["555555"]
    assert result.attempted == 1
    assert result.skipped == 1
    assert result.proofs_blocked == 0
    assert result.candidates[0] is missing_clearance
    assert result.candidates[1].typical_price == 50.0
    assert result.candidates[1].variant_attributes["exactDetailReferenceStatus"] == "trusted"


def test_capacity_overflow_quarantines_unchecked_search_reference() -> None:
    stronger = candidate("666666", price=10.0, reference=100.0)
    overflow = candidate("777777", price=20.0, reference=100.0)
    provider = FakeDetailProvider(
        {
            "666666": {
                "itemId": 666666,
                "name": "Stronger candidate",
                "salePrice": 10.0,
                "wasPrice": 100.0,
            },
            "777777": {
                "itemId": 777777,
                "name": "Overflow candidate",
                "salePrice": 20.0,
                "wasPrice": 100.0,
            },
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices(
            [stronger, overflow],
            provider=provider,
            limit=1,
            min_discount=50,
        )
    )

    assert provider.calls == ["666666"]
    assert result.proofs_blocked == 1
    blocked = result.candidates[1]
    assert blocked.current_price == 20.0
    assert blocked.typical_price is None
    assert blocked.api_reference_price is None
    assert blocked.api_discount_percent is None
    assert blocked.variant_attributes["exactDetailReferenceStatus"] == "skipped_capacity"
    assert blocked.variant_attributes["referencePriceTrusted"] == "no"
    assert blocked.variant_attributes["observedPriceFallback"] == "exact_item_baseline"


def test_provider_unavailable_quarantines_all_search_references() -> None:
    original = candidate("888888", price=15.0, reference=75.0)

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=object(), limit=24)
    )

    blocked = result.candidates[0]
    assert result.attempted == 0
    assert result.skipped == 1
    assert result.proofs_blocked == 1
    assert blocked.current_price == 15.0
    assert blocked.typical_price is None
    assert blocked.api_reference_price is None
    assert blocked.variant_attributes["exactDetailReferenceStatus"] == "provider_unavailable"


def test_autoscan_enriches_before_cards_and_price_memory() -> None:
    source = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(
        encoding="utf-8"
    )

    enrich_pos = source.index("exact_prices = await enrich_walmart_exact_prices")
    aggregate_pos = source.index("aggregate = ProviderScanResult")
    review_pos = source.index("review_candidates = build_review_candidate_cards")
    memory_pos = source.index("observed_memory = await select_observed_price_drop_cards")

    assert 'provider_registry.get("walmart")' in source
    assert "AUTOSCAN_EXACT_DETAIL_LIMIT = 24" in source
    assert "min_discount=starting_discount" in source
    assert enrich_pos < aggregate_pos < review_pos < memory_pos
