from __future__ import annotations

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.registry import ProviderRegistry
from sniperplug.providers.walmart import WalmartProvider
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.review_card_enrichment import (
    DISCOVERY_TAG_FIELD,
    LISTING_DETAILS_FIELD,
    enrich_review_card,
)
from sniperplug.services.walmart_product_metadata import extract_walmart_product_metadata
from sniperplug.services import walmart_card_renderer
from sniperplug.services import walmart_review_candidates


SCREENSHOT_LIKE_DETAIL = {
    "itemId": "403861667",
    "name": "20-Volt Battery-Powered Brushless Orbital Jigsaw",
    "productTrackingUrl": "https://www.walmart.com/ip/403861667",
    "brandName": "HART",
    "priceInfo": {
        "currentPrice": {"price": 99.97},
        "wasPrice": {"price": 146.00},
        "priceDisplayCondition": "Price when purchased online",
    },
    "clearance": True,
    "badges": [
        {"text": "Clearance"},
        {"text": "Overall pick"},
    ],
    "customerRating": 4.8,
    "numReviews": 103,
    "sellerName": "Walmart",
    "marketplace": False,
    "condition": "New",
    "availableOnline": True,
    "conditionOptions": [
        {
            "conditionDisplayName": "New",
            "currentPrice": 99.97,
            "availabilityStatus": "IN_STOCK",
            "selected": True,
        },
        {
            "conditionDisplayName": "Open Box",
            "availabilityStatus": "OUT_OF_STOCK",
        },
    ],
    "fulfillmentOptions": [
        {
            "type": "SHIPPING",
            "availabilityStatus": "IN_STOCK",
            "message": "Free shipping, arrives Tue, Aug 4",
        },
        {
            "type": "PICKUP",
            "availabilityStatus": "CHECK_NEARBY",
            "message": "Check nearby",
        },
        {
            "type": "DELIVERY",
            "availabilityStatus": "NOT_AVAILABLE",
        },
    ],
    "returnPolicy": {"returnWindow": "90-day returns"},
    "fulfillmentLocation": {
        "city": "Bridgeport",
        "state": "CT",
        "postalCode": "06610",
    },
}


def test_metadata_extractor_captures_all_returned_listing_facts() -> None:
    metadata = extract_walmart_product_metadata(
        SCREENSHOT_LIKE_DETAIL,
        current_price=99.97,
        reference_price=146.00,
        exact_detail=True,
    )
    attrs = metadata.attributes

    assert attrs["retailerMetadataSource"] == "exact_detail"
    assert attrs["retailerTags"] == "Clearance | Overall Pick"
    assert attrs["officialSavingsAmount"] == "46.03"
    assert attrs["rating"] == "4.8"
    assert attrs["reviews"] == "103"
    assert attrs["purchaseContext"] == "Price when purchased online"
    assert "New — Available — $99.97" in attrs["conditionOptions"]
    assert "Open Box — Out of stock" in attrs["conditionOptions"]
    assert attrs["shippingStatus"] == "Available"
    assert attrs["shippingText"] == "Free shipping, arrives Tue, Aug 4"
    assert attrs["pickupStatus"] == "Check nearby"
    assert attrs["deliveryStatus"] == "Not Available"
    assert attrs["returnPolicy"] == "90-day returns"
    assert attrs["fulfillmentLocation"] == "Bridgeport, CT, 06610"


def test_metadata_extractor_does_not_invent_missing_page_facts() -> None:
    metadata = extract_walmart_product_metadata(
        {"itemId": "1", "name": "Plain product"},
        current_price=10.0,
        reference_price=None,
        exact_detail=True,
    )
    attrs = metadata.attributes

    assert "retailerTags" not in attrs
    assert "officialSavingsAmount" not in attrs
    assert "shippingStatus" not in attrs
    assert "pickupStatus" not in attrs
    assert "deliveryStatus" not in attrs
    assert "returnPolicy" not in attrs
    assert "fulfillmentLocation" not in attrs


def test_registered_walmart_provider_enriches_exact_candidate_and_both_renderers() -> None:
    provider = WalmartProvider(configured=False)
    registry = ProviderRegistry()
    registry.register(provider)

    candidate = provider._candidate_from_item(
        SCREENSHOT_LIKE_DETAIL,
        request=ProviderScanRequest(
            source_key="walmart_exact_detail",
            query="403861667",
            max_results=1,
            metadata={"exact_detail_price_check": "yes"},
        ),
    )
    assert candidate is not None
    attrs = candidate.variant_attributes
    assert attrs["retailerTags"] == "Clearance | Overall Pick"
    assert attrs["officialSavingsAmount"] == "46.03"
    assert attrs["retailerMetadataSource"] == "exact_detail"

    deal = candidate.to_normalized_deal()
    proof = verified_deal_value(deal)

    public_offer_text = "\n".join(walmart_card_renderer.offer_lines(candidate, deal))
    public_fulfillment_text = "\n".join(
        walmart_card_renderer.fulfillment_lines(candidate, deal)
    )
    assert "Walmart tags" in public_offer_text
    assert "Clearance | Overall Pick" in public_offer_text
    assert "Other condition offers" in public_offer_text
    assert "Shipping" in public_fulfillment_text
    assert "Pickup" in public_fulfillment_text
    assert "Delivery" in public_fulfillment_text
    assert "90-day returns" in public_fulfillment_text
    assert "Bridgeport, CT, 06610" in public_fulfillment_text

    review_card = walmart_review_candidates.build_review_card(
        candidate,
        deal,
        proof,
        context_price=None,
        context_discount=None,
        ignored_context_price=None,
        coupon=0.0,
        cash=0.0,
    )
    assert review_card.variant_attributes["retailerTags"] == "Clearance | Overall Pick"
    assert review_card.candidate is candidate

    enrich_review_card(review_card)
    fields = {field.name: field.value for field in review_card.embed.fields}
    assert "`Clearance`" in fields[DISCOVERY_TAG_FIELD]
    assert "`Overall Pick`" in fields[DISCOVERY_TAG_FIELD]
    assert "You save" in fields[LISTING_DETAILS_FIELD]
    assert "$46.03" in fields[LISTING_DETAILS_FIELD]
    assert "Open Box" in fields[LISTING_DETAILS_FIELD]
    assert "Shipping" in fields[LISTING_DETAILS_FIELD]
    assert "Pickup" in fields[LISTING_DETAILS_FIELD]
    assert "Delivery" in fields[LISTING_DETAILS_FIELD]
    assert "90-day returns" in fields[LISTING_DETAILS_FIELD]
