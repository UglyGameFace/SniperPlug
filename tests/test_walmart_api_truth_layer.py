from types import SimpleNamespace

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.direct_search_rescue import direct_match_score
from sniperplug.services.walmart_api_value_proof import extract_walmart_api_value_proof


def test_buy_more_save_api_promo_is_preserved_from_payload():
    item = {
        "name": "Joyfy Montessori Learning Toys for Toddlers",
        "itemId": 14525201846,
        "upc": "840332680304",
        "salePrice": 18.99,
        "savingsAmount": 21.00,
        "msrp": 39.99,
        "availableOnline": True,
        "stock": "Available",
        "offerType": "ONLINE_AND_STORE",
        "sellerName": "Walmart",
        "promotions": [
            {"title": "Buy more, save up to $10", "urlText": "View eligible items"},
        ],
    }

    proof = extract_walmart_api_value_proof(item, current_price=18.99)

    assert proof["apiSavingsAmount"] == "21.00"
    assert proof["apiReferenceFromSavings"] == "39.99"
    assert proof["apiPromotionSavingsCap"] == "10.00"
    assert "Buy more, save up to $10" in proof["apiPromotionText"]
    assert proof["apiValueKind"] == "buy_more_save_promo"


def test_walmart_provider_attaches_api_value_proof_to_candidate():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="test", private_key_b64="test"))
    item = {
        "name": "Joyfy Montessori Learning Toys for Toddlers",
        "itemId": 14525201846,
        "upc": "840332680304",
        "salePrice": 18.99,
        "savingsAmount": 21.00,
        "msrp": 39.99,
        "availableOnline": True,
        "stock": "Available",
        "sellerName": "Walmart",
        "promotions": [{"name": "Buy more, save up to $10 | View eligible items"}],
    }

    candidate = provider._candidate_from_item(item, request=SimpleNamespace())
    assert candidate is not None

    attrs = candidate.variant_attributes
    assert attrs["apiSavingsAmount"] == "21.00"
    assert attrs["apiReferenceFromSavings"] == "39.99"
    assert attrs["apiPromotionSavingsCap"] == "10.00"
    assert "Buy more" in attrs["apiPromotionText"]

    deal = candidate.to_normalized_deal()
    notes = "\n".join(deal.verification_notes)
    assert "Walmart API savings from payload" in notes
    assert "Walmart API promo" in notes


def test_generic_category_route_is_not_exact_product_match():
    assert direct_match_score(
        "toy clearance",
        "Joyfy Montessori Learning Toys for Toddlers 1 2 3 Years Old",
        sku="14525201846",
        upc="840332680304",
        product_id="14525201846",
    ) < 0.45


def test_specific_product_query_can_still_match():
    assert direct_match_score(
        "joyfy montessori learning toys",
        "Joyfy Montessori Learning Toys for Toddlers 1 2 3 Years Old",
    ) >= 0.45


def test_identifier_query_still_counts_as_exact_match():
    assert direct_match_score(
        "14525201846",
        "Joyfy Montessori Learning Toys for Toddlers",
        sku="14525201846",
        product_id="14525201846",
    ) == 1.0
