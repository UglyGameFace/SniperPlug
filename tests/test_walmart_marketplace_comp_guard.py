from __future__ import annotations

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_marketplace_comp_guard import install_walmart_marketplace_comp_guard
from sniperplug.services.walmart_review_candidates import build_review_candidate_cards


install_walmart_marketplace_comp_guard()


def test_best_marketplace_price_is_not_reference_context_math():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 12024768241,
        "name": "Straight Talk Samsung Galaxy A16, 128GB, 5G, Black - Prepaid Smartphone",
        "salePrice": 39.88,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=12024768241",
        "stock": "Available",
        "availableOnline": True,
        "maxItemsInOrder": 2,
        "bestMarketplacePrice": {"price": 96.00},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="galaxy a16"))

    assert candidate is not None
    assert candidate.typical_price is None
    assert candidate.variant_attributes.get("referenceContextPrice") is None
    assert candidate.variant_attributes["marketplaceCompPrice"] == "96.00"
    assert candidate.variant_attributes["marketplaceCompSource"] == "bestMarketplacePrice.price"


def test_review_card_labels_marketplace_comp_as_flip_context_not_discount_proof():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Straight Talk Samsung Galaxy A16, 128GB, 5G, Black - Prepaid Smartphone",
        product_url="https://www.walmart.com/ip/12024768241",
        current_price=39.88,
        typical_price=None,
        sku="12024768241",
        variant_attributes={
            "marketplaceCompPrice": "96.00",
            "marketplaceCompSource": "bestMarketplacePrice.price",
            "marketplaceCompNote": "Walmart API marketplace comp; not was/regular price; use for flip research only",
        },
        signals=("rollback",),
    )

    result = build_review_candidate_cards([candidate])

    assert len(result.cards) == 1
    rendered = str(result.cards[0].embed.to_dict())
    assert "Marketplace comp" in rendered
    assert "bestMarketplacePrice.price" in rendered
    assert "flip research only" in rendered
    assert "Context math" not in rendered
