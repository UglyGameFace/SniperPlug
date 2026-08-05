from __future__ import annotations

import asyncio

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_price_enrichment import (
    enrich_walmart_exact_prices,
    exact_detail_verified_candidates,
)
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity


class FakeDetailProvider:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.inner = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        return self.payloads[item_id]


def search_candidate(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Search item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=20.0,
        typical_price=100.0,
        api_current_price=20.0,
        api_reference_price=100.0,
        api_reference_path="search.wasPrice",
        api_discount_percent=80.0,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes={
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "100.00",
            "trustedReferenceSource": "search.wasPrice",
        },
    )


def test_non_marketplace_detail_without_seller_is_bound_to_walmart() -> None:
    original = search_candidate("123456")
    provider = FakeDetailProvider(
        {
            "123456": {
                "itemId": 123456,
                "name": "Exact Walmart-owned item",
                "salePrice": 19.99,
                "wasPrice": 49.99,
                "isMarketPlaceItem": False,
                "availableOnline": True,
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    exact = result.candidates[0]
    assert result.enriched == 1
    assert result.offer_identity_blocked == 0
    assert exact.seller_name == "Walmart"
    assert exact.variant_attributes["walmartSeller"] == "yes"
    assert exact.variant_attributes["isMarketPlaceItem"] == "no"
    assert exact.variant_attributes["exactDetailSellerIdentityStatus"] == "verified"
    assert exact.variant_attributes["exactDetailSellerIdentitySource"] == "isMarketPlaceItem=false"
    assert exact_offer_identity(exact) is not None
    assert exact_detail_verified_candidates(result.candidates) == [exact]


def test_marketplace_detail_without_seller_fails_closed() -> None:
    original = search_candidate("222222")
    provider = FakeDetailProvider(
        {
            "222222": {
                "itemId": 222222,
                "name": "Marketplace item with missing seller",
                "salePrice": 29.99,
                "wasPrice": 69.99,
                "isMarketPlaceItem": True,
                "availableOnline": True,
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    exact = result.candidates[0]
    assert result.enriched == 1
    assert result.offer_identity_blocked == 1
    assert exact.variant_attributes["isMarketPlaceItem"] == "yes"
    assert exact.variant_attributes["exactDetailSellerIdentityStatus"] == "missing"
    assert exact.variant_attributes["exactDetailOfferIdentityStatus"] == "blocked"
    assert exact_offer_identity(exact) is None
    assert exact_detail_verified_candidates(result.candidates) == []


def test_marketplace_identity_is_preserved_but_unknown_shipping_blocks_price() -> None:
    original = search_candidate("333333")
    provider = FakeDetailProvider(
        {
            "333333": {
                "itemId": 333333,
                "name": "Marketplace item with seller",
                "salePrice": 39.99,
                "wasPrice": 79.99,
                "isMarketPlaceItem": True,
                "sellerName": "Exact Seller LLC",
                "sellerId": "seller-123",
                "offerId": "offer-456",
                "availableOnline": True,
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    exact = result.candidates[0]
    identity = exact_offer_identity(exact)
    assert result.offer_identity_blocked == 0
    assert exact.seller_name == "Exact Seller LLC"
    assert exact.selected_offer_id == "offer-456"
    assert exact.variant_attributes["sellerId"] == "seller-123"
    assert exact.variant_attributes["walmartSeller"] == "no"
    assert identity is not None
    assert identity.offer_id == "offer-456"
    assert identity.seller_key == "id:seller-123"
    assert exact.item_price == 39.99
    assert exact.shipping_status == "unknown"
    assert exact.current_price is None
    assert exact.api_current_price is None
    assert exact.delivered_price is None
    assert exact.variant_attributes.get("deliveredPrice") in {None, ""}
    assert exact.variant_attributes["selectedOfferPublicPriceStatus"] == "blocked_shipping_unknown"
    assert exact_detail_verified_candidates(result.candidates) == []


def test_marketplace_detail_with_explicit_free_shipping_is_payable_price_verified() -> None:
    original = search_candidate("444444")
    provider = FakeDetailProvider(
        {
            "444444": {
                "itemId": 444444,
                "name": "Marketplace item with free shipping",
                "salePrice": 39.99,
                "wasPrice": 79.99,
                "isMarketPlaceItem": True,
                "sellerName": "Exact Free Ship LLC",
                "sellerId": "seller-free",
                "offerId": "offer-free",
                "freeShipping": True,
                "availableOnline": True,
            }
        }
    )

    result = asyncio.run(
        enrich_walmart_exact_prices([original], provider=provider, limit=1)
    )

    exact = result.candidates[0]
    identity = exact_offer_identity(exact)
    assert result.offer_identity_blocked == 0
    assert exact.seller_name == "Exact Free Ship LLC"
    assert exact.selected_offer_id == "offer-free"
    assert exact.item_price == 39.99
    assert exact.shipping_cost == 0.0
    assert exact.shipping_status == "free"
    assert exact.delivered_price == 39.99
    assert exact.current_price == 39.99
    assert exact.api_current_price == 39.99
    assert identity is not None
    assert identity.offer_id == "offer-free"
    assert identity.seller_key == "id:seller-free"
    assert exact_detail_verified_candidates(result.candidates) == [exact]
