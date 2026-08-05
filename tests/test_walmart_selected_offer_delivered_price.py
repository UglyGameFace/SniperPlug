from __future__ import annotations

import asyncio

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderScanResult
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.walmart_card_renderer import (
    build_walmart_cards,
    price_lines,
)
from sniperplug.services.walmart_exact_price_enrichment import (
    enrich_walmart_exact_prices,
    exact_detail_verified_candidates,
)
from sniperplug.services.walmart_marketplace_comp import (
    marketplace_comp_from_item,
)


def provider() -> WalmartProvider:
    return WalmartProvider(
        WalmartAffiliateConfig(
            enabled=True,
            consumer_id="test",
            private_key_b64="unused",
        )
    )


def request() -> ProviderScanRequest:
    return ProviderScanRequest(source_key="walmart", query="test")


def test_selected_offer_price_shipping_and_reference_override_other_sellers() -> None:
    item = {
        "itemId": 700001,
        "name": "Marketplace product",
        "minPrice": 3.50,
        "wasPrice": 999.00,
        "isMarketPlaceItem": True,
        "availableOnline": True,
        "selectedOffer": {
            "offerId": "offer-selected",
            "sellerId": "seller-a",
            "sellerName": "Seller A",
            "currentPrice": {"amount": 20.00},
            "wasPrice": {"amount": 100.00},
            "shippingCost": {"amount": 15.00},
            "fulfillmentType": "SHIPPING",
            "condition": "New",
            "isMarketPlaceItem": True,
        },
    }

    candidate = provider()._candidate_from_item(item, request())

    assert candidate is not None
    assert candidate.selected_offer_id == "offer-selected"
    assert candidate.seller_name == "Seller A"
    assert candidate.item_price == 20.00
    assert candidate.shipping_cost == 15.00
    assert candidate.delivered_price == 35.00
    assert candidate.current_price == 35.00
    assert candidate.api_current_price == 35.00
    assert candidate.typical_price == 100.00
    assert candidate.api_reference_price == 100.00
    assert candidate.api_reference_path == "selectedOffer.wasPrice"
    assert candidate.api_discount_percent == 65.00
    assert candidate.variant_attributes["trustedReferencePrice"] == "100.00"
    assert (
        candidate.variant_attributes["trustedReferenceSource"]
        == "selectedOffer.wasPrice"
    )
    assert candidate.variant_attributes["alternateSellerMinPrice"] == "3.50"
    assert (
        candidate.variant_attributes["selectedOfferPublicPriceStatus"]
        == "verified_delivered"
    )
    assert candidate.variant_attributes["currentPriceSource"].endswith(
        "shippingCost"
    )


def test_nested_offer_without_same_offer_reference_blocks_page_was_price() -> None:
    item = {
        "itemId": 700008,
        "name": "Cross-offer reference product",
        "wasPrice": 100.00,
        "selectedOffer": {
            "offerId": "offer-no-reference",
            "sellerId": "seller-no-reference",
            "sellerName": "Seller No Reference",
            "currentPrice": 20.00,
            "shippingCost": 10.00,
            "isMarketPlaceItem": True,
        },
    }

    candidate = provider()._candidate_from_item(item, request())

    assert candidate is not None
    assert candidate.current_price == 30.00
    assert candidate.item_price == 20.00
    assert candidate.shipping_cost == 10.00
    assert candidate.typical_price is None
    assert candidate.api_reference_price is None
    assert candidate.api_reference_path is None
    assert candidate.api_discount_percent is None
    assert candidate.variant_attributes["referencePriceTrusted"] == "no"
    assert candidate.variant_attributes["crossOfferReferenceBlocked"] == "yes"
    assert (
        candidate.variant_attributes["selectedOfferReferenceStatus"]
        == "blocked_cross_offer_reference"
    )
    assert candidate.variant_attributes["referenceContextPrice"] == "100.00"
    assert any(
        "not proven for selected seller/offer" in signal
        for signal in candidate.signals
    )

    cards = build_walmart_cards(
        ProviderScanResult(
            provider_key="walmart",
            candidates=(candidate,),
        ),
        min_discount=50,
        alerts_only=False,
    )
    assert cards == []


def test_explicit_free_shipping_keeps_selected_offer_item_price() -> None:
    item = {
        "itemId": 700002,
        "name": "Free shipping marketplace product",
        "wasPrice": 999.00,
        "selectedOffer": {
            "offerId": "offer-free",
            "sellerId": "seller-free",
            "sellerName": "Free Ship Seller",
            "salePrice": 24.00,
            "wasPrice": 80.00,
            "freeShipping": True,
            "isMarketPlaceItem": True,
        },
    }

    candidate = provider()._candidate_from_item(item, request())

    assert candidate is not None
    assert candidate.current_price == 24.00
    assert candidate.item_price == 24.00
    assert candidate.shipping_cost == 0.00
    assert candidate.delivered_price == 24.00
    assert candidate.shipping_status == "free"
    assert candidate.typical_price == 80.00
    assert candidate.api_reference_path == "selectedOffer.wasPrice"
    assert candidate.api_discount_percent == 70.00


def test_unknown_marketplace_shipping_fails_closed() -> None:
    item = {
        "itemId": 700003,
        "name": "Unknown shipping marketplace product",
        "wasPrice": 100.00,
        "selectedOffer": {
            "offerId": "offer-unknown",
            "sellerId": "seller-unknown",
            "sellerName": "Unknown Ship Seller",
            "currentPrice": 10.00,
            "wasPrice": 100.00,
            "isMarketPlaceItem": True,
        },
    }

    candidate = provider()._candidate_from_item(item, request())

    assert candidate is not None
    assert candidate.item_price == 10.00
    assert candidate.shipping_status == "unknown"
    assert candidate.shipping_cost is None
    assert candidate.delivered_price is None
    assert candidate.current_price is None
    assert candidate.api_current_price is None
    assert candidate.api_discount_percent is None
    assert candidate.variant_attributes.get("deliveredPrice") in {None, ""}
    assert (
        candidate.variant_attributes["selectedOfferPublicPriceStatus"]
        == "blocked_shipping_unknown"
    )
    assert any(
        "shipping cost was not returned" in signal
        for signal in candidate.signals
    )

    cards = build_walmart_cards(
        ProviderScanResult(
            provider_key="walmart",
            candidates=(candidate,),
        ),
        min_discount=50,
        alerts_only=False,
    )
    assert cards == []


def test_page_minimum_without_selected_offer_is_context_only() -> None:
    item = {
        "itemId": 700004,
        "name": "Multiple sellers product",
        "minPrice": 5.00,
        "wasPrice": 50.00,
        "isMarketPlaceItem": True,
        "sellerName": "Some Seller",
    }

    candidate = provider()._candidate_from_item(item, request())

    assert candidate is not None
    assert candidate.current_price is None
    assert candidate.api_current_price is None
    assert candidate.variant_attributes["alternateSellerMinPrice"] == "5.00"
    assert (
        candidate.variant_attributes["selectedOfferPublicPriceStatus"]
        == "blocked_alternate_min_price"
    )


def test_walmart_owned_offer_without_shipping_claims_checkout_dependent_not_free() -> None:
    item = {
        "itemId": 700005,
        "name": "Walmart owned product",
        "salePrice": 18.00,
        "wasPrice": 60.00,
        "isMarketPlaceItem": False,
        "availableOnline": True,
    }

    candidate = provider()._candidate_from_item(item, request())

    assert candidate is not None
    assert candidate.seller_name == "Walmart"
    assert candidate.current_price == 18.00
    assert candidate.item_price == 18.00
    assert candidate.typical_price == 60.00
    assert candidate.api_reference_path == "item.wasPrice"
    assert candidate.delivered_price is None
    assert candidate.shipping_cost is None
    assert candidate.shipping_status == "checkout_dependent"
    assert candidate.variant_attributes.get("deliveredPrice") in {None, ""}
    assert (
        candidate.variant_attributes["selectedOfferPublicPriceStatus"]
        == "item_price_shipping_checkout_dependent"
    )


def test_exact_marketplace_offer_with_unknown_shipping_is_not_verified_for_public_use() -> None:
    original = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Search result",
        product_url="https://www.walmart.com/ip/700006",
        direct_product_url="https://www.walmart.com/ip/700006",
        current_price=5.00,
        typical_price=100.00,
        api_current_price=5.00,
        api_reference_price=100.00,
        api_reference_path="search.wasPrice",
        api_discount_percent=95.00,
        product_id="700006",
        product_id_type="sku",
        sku="700006",
        selected_offer_id="700006",
        variant_attributes={
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "100.00",
            "trustedReferenceSource": "search.wasPrice",
        },
    )

    class DetailProvider:
        inner = provider()

        async def fetch_product_detail_payload(self, item_id: str) -> dict:
            assert item_id == "700006"
            return {
                "itemId": 700006,
                "name": "Exact marketplace result",
                "wasPrice": 100.00,
                "selectedOffer": {
                    "offerId": "offer-exact",
                    "sellerId": "seller-exact",
                    "sellerName": "Exact Seller",
                    "currentPrice": 5.00,
                    "wasPrice": 100.00,
                    "isMarketPlaceItem": True,
                },
            }

    result = asyncio.run(
        enrich_walmart_exact_prices(
            [original],
            provider=DetailProvider(),
            limit=1,
        )
    )

    exact = result.candidates[0]
    assert exact.selected_offer_id == "offer-exact"
    assert exact.seller_name == "Exact Seller"
    assert exact.current_price is None
    assert exact.delivered_price is None
    assert exact.variant_attributes.get("deliveredPrice") in {None, ""}
    assert exact_detail_verified_candidates(result.candidates) == []


def test_card_displays_item_shipping_delivered_and_other_seller_context() -> None:
    item = {
        "itemId": 700007,
        "name": "Rendered marketplace product",
        "minPrice": 9.00,
        "wasPrice": 999.00,
        "selectedOffer": {
            "offerId": "offer-rendered",
            "sellerId": "seller-rendered",
            "sellerName": "Rendered Seller",
            "currentPrice": 20.00,
            "wasPrice": 100.00,
            "shippingPrice": 10.00,
            "isMarketPlaceItem": True,
        },
    }
    candidate = provider()._candidate_from_item(item, request())
    assert candidate is not None
    deal = candidate.to_normalized_deal()
    proof = verified_deal_value(deal)

    rendered = "\n".join(price_lines(candidate, deal, proof))

    assert "Selected-offer item price: **$20.00**" in rendered
    assert "Shipping: **$10.00**" in rendered
    assert "Delivered total used for deal math: **$30.00**" in rendered
    assert "Was/typical: ~~$100.00~~" in rendered
    assert "Other-seller minimum: **$9.00**" in rendered
    assert "context only, not this selected offer" in rendered


def test_marketplace_comp_extractor_keeps_selected_and_alternate_prices_separate() -> None:
    attrs = marketplace_comp_from_item(
        {
            "minPrice": 4.00,
            "wasPrice": 999.00,
            "bestMarketplacePrice": {
                "price": 75.00,
                "sellerName": "Other Seller",
            },
            "selectedOffer": {
                "offerId": "selected",
                "sellerName": "Selected Seller",
                "currentPrice": 30.00,
                "wasPrice": 60.00,
                "shippingCost": 8.00,
                "isMarketPlaceItem": True,
            },
        }
    )

    assert attrs["selectedOfferItemPrice"] == "30.00"
    assert attrs["selectedOfferShippingCost"] == "8.00"
    assert attrs["selectedOfferDeliveredPrice"] == "38.00"
    assert attrs["selectedOfferReferencePrice"] == "60.00"
    assert attrs["selectedOfferReferenceSource"] == "selectedOffer.wasPrice"
    assert attrs["alternateSellerMinPrice"] == "4.00"
    assert attrs["marketplaceCompPrice"] == "75.00"
