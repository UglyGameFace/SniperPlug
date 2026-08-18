from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart_variant import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.candidate_pipeline import evaluate_candidate


def test_walmart_platform_variant_mismatch_flags_xbox_and_blocks_public_alert():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 999111,
        "offerId": "offer-xbox",
        "name": "Acme Pro Wireless Gaming Headset for PS5",
        "salePrice": 24.99,
        "msrp": 129.99,
        "upc": "123456789012",
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=999111",
        "stock": "Available",
        "availableOnline": True,
        "variantAttributes": {"platform": "Xbox", "color": "Black"},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="gaming headset"))

    assert candidate is not None
    assert candidate.platform == "Xbox"
    assert candidate.variant_label.startswith("Xbox")
    assert "Selected option: Xbox" in candidate.title
    assert "Parent listing" in candidate.title
    assert candidate.parent_title == "Acme Pro Wireless Gaming Headset for PS5"
    assert candidate.option_mismatch_warning
    assert "PlayStation" in candidate.option_mismatch_warning
    assert "Xbox" in candidate.option_mismatch_warning

    decision = evaluate_candidate(candidate)
    assert decision.should_alert is False
    assert decision.hold_for_review is True
    assert decision.deal.requires_staff_review is True


def test_walmart_pack_size_mismatch_flags_staff_review_not_price_glitch():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 49692798,
        "offerId": "single-offer",
        "name": "Turtle Wax 50597 Max-Power 3 Levels of Cleaning Car Wash, 4 Pack",
        "salePrice": 6.97,
        "msrp": 94.99,
        "upc": "074660505977",
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=49692798",
        "stock": "Available",
        "availableOnline": True,
        "variantAttributes": {"packSize": "Single"},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="car wash"))

    assert candidate is not None
    assert candidate.pack_size == "Single"
    assert candidate.option_mismatch_warning
    assert "pack/size" in candidate.option_mismatch_warning
    assert candidate.requires_staff_review is True

    decision = evaluate_candidate(candidate)
    assert decision.deal.requires_staff_review is True
    assert decision.deal.is_possible_price_error is False
    assert decision.should_alert is False
    assert decision.hold_for_review is True


def test_variant_fields_carry_into_normalized_deal_notes():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 12345,
        "offerId": "offer-blue",
        "name": "Example Widget",
        "salePrice": 20.0,
        "msrp": 40.0,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=12345",
        "variantAttributes": {"color": "Blue", "modelNumber": "ABC-1"},
    }

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="widget"))
    assert candidate is not None

    deal = candidate.to_normalized_deal()
    assert deal.selected_offer_id == "offer-blue"
    assert deal.color == "Blue"
    assert deal.model == "ABC-1"
    assert deal.variant_label == "Blue, ABC-1"
    assert "Selected option: Blue, ABC-1" in deal.verification_notes
