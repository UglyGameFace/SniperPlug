from __future__ import annotations

import pathlib

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.listing_contradiction import (
    build_listing_error_lead_card,
    candidate_condition,
    candidate_contradiction,
    detect_condition_contradiction,
    detect_listing_contradiction,
)


def cand(**overrides) -> SourceCandidate:
    base = dict(
        source_key="walmart",
        retailer="Walmart",
        title="Generic Product 20-Pack",
        product_url="https://www.walmart.com/ip/123456789",
        current_price=9.00,
        stock_status="In Stock",
        can_add_to_cart=True,
    )
    base.update(overrides)
    return SourceCandidate(**base)


def detect(title, description=None, **kw):
    return detect_listing_contradiction(title=title, description=description, **kw)


# --- the original case (one fixture, not the design center) -----------------

def test_original_case_flags_and_leads():
    result = detect(
        "Little Hotties Foot Warmers 20-Pack",
        "Each package contains 5 pairs of warmers.",
        current_price=9.00,
        reference_prices=[66.00],
    )
    assert result is not None
    assert result.is_lead
    assert result.title_claim.value == 20
    assert result.other_claim.value == 10  # 5 pairs normalized to 10 items
    assert "may receive the smaller amount" in result.note()


# --- positives across unrelated categories ----------------------------------

def test_batteries_count_mismatch():
    r = detect("AA Batteries 48-Pack", "Package includes 24 count of AA cells.",
               current_price=10.0, reference_prices=[30.0])
    assert r is not None and r.is_lead


def test_wipes_total_count_mismatch():
    r = detect("Baby Wipes 12 Packs, 720 Wipes", "You receive 480 wipes in total.",
               current_price=12.0, reference_prices=[28.0])
    assert r is not None and r.is_lead


def test_laundry_count_mismatch():
    r = detect("Laundry Detergent Sheets 90 Count", "Contains 60 sheets per box.",
               current_price=8.0, reference_prices=[20.0])
    assert r is not None and r.is_lead


def test_pairs_vs_pairs_mismatch():
    r = detect("Crew Socks 12 Pairs", "Includes 6 pairs of socks.",
               current_price=7.0, reference_prices=[18.0])
    assert r is not None and r.is_lead
    assert r.title_claim.value == 24 and r.other_claim.value == 12


def test_rolls_mismatch():
    r = detect("Paper Towels 12 Rolls", "This pack includes 6 rolls.",
               current_price=6.0, reference_prices=[15.0])
    assert r is not None and r.is_lead


def test_capsules_mismatch():
    r = detect("Vitamin D3 120 Capsules", "Each bottle contains 60 capsules.",
               current_price=9.0, reference_prices=[22.0])
    assert r is not None and r.is_lead


def test_bars_mismatch():
    r = detect("Protein Bars 24-Pack", "Box contains 12 bars.",
               current_price=11.0, reference_prices=[26.0])
    assert r is not None and r.is_lead


def test_weight_mismatch():
    r = detect("Ground Coffee 64 oz", "Net weight 32 oz per bag.",
               current_price=9.0, reference_prices=[24.0])
    assert r is not None and r.is_lead
    assert r.title_claim.dimension == "weight"


def test_volume_mismatch():
    r = detect("Dish Soap 64 fl oz", "Bottle size is 32 fl oz.",
               current_price=5.0, reference_prices=[13.0])
    assert r is not None and r.is_lead
    assert r.title_claim.dimension == "volume"


def test_variant_only_mismatch_still_flags():
    c = cand(title="Trail Mix 40-Pack", variant_attributes={"packSize": "20 Pack"},
             current_price=10.0, typical_price=30.0)
    r = candidate_contradiction(c)
    assert r is not None and r.other_source == "variant" and r.is_lead


# --- negatives ---------------------------------------------------------------

def test_pairs_normalization_matching_counts_no_flag():
    # 20 individual items == 10 pairs: same quantity, different wording.
    r = detect("Hand Warmers 20 Count", "Contains 10 pairs.")
    assert r is None


def test_matching_counts_other_categories_no_flag():
    assert detect("AA Batteries 48-Pack", "Includes 48 count.") is None
    assert detect("Vitamin C 120 Capsules", "Bottle contains 120 capsules.") is None
    assert detect("Ground Coffee 64 oz", "Net weight 64 oz (4 lb).") is None


def test_no_description_no_variant_no_flag():
    assert detect("AA Batteries 48-Pack", None) is None


def test_incomparable_units_no_flag():
    # Count in the title, weight in the description: never compared.
    assert detect("Trail Mix 20-Pack", "Net weight 32 oz.") is None
    # Weight vs volume are different dimensions too.
    assert detect("Shampoo 32 oz", "Bottle holds 500 ml of product.") is None


def test_ambiguous_description_no_flag():
    # Description carries two different counts for the same unit (a main
    # pack plus a differently-sized refill mention): fail closed.
    r = detect("AA Batteries 48-Pack",
               "Includes 24 count. A 48 count refill is also available.")
    assert r is None


def test_small_difference_is_noise_no_flag():
    r = detect("Granola Bars 24-Pack", "Box contains 20 bars.")
    assert r is None  # ratio 1.2 < 1.5


def test_contradiction_without_value_evidence_is_not_a_lead():
    r = detect("AA Batteries 48-Pack", "Includes 24 count.",
               current_price=10.0, reference_prices=[])
    assert r is not None and not r.is_lead
    assert build_listing_error_lead_card(
        cand(title="AA Batteries 48-Pack", description="Includes 24 count.",
             current_price=10.0)
    ) is None


# --- provider + pipeline wiring ----------------------------------------------

def test_walmart_provider_carries_description():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "name": "AA Batteries 48-Pack",
        "itemId": 123456789,
        "productTrackingUrl": "https://www.walmart.com/ip/123456789",
        "salePrice": 10.0,
        "longDescription": "<p>Includes <b>24 count</b> of AA cells.</p>",
        "stock": "Available",
        "availableOnline": True,
    }
    from sniperplug.providers.base import ProviderScanRequest

    candidate = provider._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="batteries"))
    assert candidate is not None
    assert candidate.description == "Includes 24 count of AA cells."


def test_pipeline_flags_original_case_as_review_lead_not_alert():
    c = cand(
        title="Little Hotties Foot Warmers 20-Pack",
        description="Each package contains 5 pairs of warmers.",
        variant_attributes={"referenceContextPrice": "66.00", "referencePriceTrusted": "no"},
    )
    decision = evaluate_candidate(c)
    assert decision.should_alert is False
    assert decision.hold_for_review is True
    assert decision.deal.is_possible_price_error is True
    assert "Listing error lead" in decision.deal.alert_tags
    assert any("Listing contradiction" in flag for flag in decision.deal.risk_flags)


def test_pipeline_ordinary_item_unchanged():
    c = cand(title="AA Batteries 48-Pack", description="Includes 48 count.",
             current_price=10.0)
    decision = evaluate_candidate(c)
    assert decision.should_alert is False
    assert not any("Listing contradiction" in flag for flag in decision.deal.risk_flags)


# --- lead card honesty ---------------------------------------------------------

def test_lead_card_is_speculative_and_names_seller_and_condition():
    c = cand(
        title="Wireless Earbuds 2-Pack, Brand New",
        description="Includes 1 pack. Condition: used - like new.",
        current_price=15.0,
        typical_price=60.0,
        seller_name="ThirdPartyDeals LLC",
        condition="Used - Like New",
    )
    card = build_listing_error_lead_card(c)
    assert card is not None and card.listing_error_lead is True
    text = " ".join(f.value for f in card.embed.fields)
    assert "NOT a verified markdown" in " ".join(f.name for f in card.embed.fields)
    assert "ThirdPartyDeals LLC" in text
    assert "Like New" in text and "NOT a new item" in text
    assert "%" not in text  # no discount percentage is ever claimed
    assert "may receive the smaller amount" in text


# --- vendors -------------------------------------------------------------------

def test_marketplace_seller_listing_error_detected_and_named():
    c = cand(
        title="AA Batteries 48-Pack",
        description="Includes 24 count.",
        current_price=10.0,
        typical_price=30.0,
        seller_name="MarketplaceSellerXYZ",
    )
    r = candidate_contradiction(c)
    assert r is not None and r.is_lead
    card = build_listing_error_lead_card(c)
    assert card is not None
    text = " ".join(f.value for f in card.embed.fields)
    assert "MarketplaceSellerXYZ" in text


def test_cross_vendor_comp_price_counts_as_value_evidence():
    c = cand(
        title="AA Batteries 48-Pack",
        description="Includes 24 count.",
        current_price=10.0,
        variant_attributes={"marketplaceCompPrice": "29.99",
                            "marketplaceCompSource": "bestMarketplacePrice.price"},
    )
    r = candidate_contradiction(c)
    assert r is not None and r.is_lead


# --- conditions ------------------------------------------------------------------

def test_like_new_quantity_mismatch_detected_and_labelled():
    c = cand(
        title="Headphones 2-Pack",
        description="Includes 1 pack only.",
        current_price=20.0,
        typical_price=70.0,
        condition="Used - Like New",
    )
    assert candidate_condition(c) == "Like New"
    card = build_listing_error_lead_card(c)
    assert card is not None
    text = " ".join(f.value for f in card.embed.fields)
    assert "Like New" in text and "NOT a new item" in text
    assert "same-condition comp" in text  # new-price comparison disclosed


def test_good_condition_mismatch_detected():
    c = cand(
        title="Blender 2-Pack",
        description="Includes 1 pack.",
        current_price=18.0,
        typical_price=55.0,
        condition="Used - Good",
    )
    assert candidate_condition(c) == "Good"
    card = build_listing_error_lead_card(c)
    assert card is not None
    text = " ".join(f.value for f in card.embed.fields)
    assert "Condition: Good" in text


def test_condition_contradiction_flagged():
    c = cand(
        title="Smart Watch - Brand New",
        description="Fully tested and working.",
        condition="Used - Like New",
        current_price=50.0,
        typical_price=50.0,
    )
    clash = detect_condition_contradiction(c)
    assert clash is not None
    assert clash.title_grade == "New" and clash.other_grade == "Like New"
    decision = evaluate_candidate(c)
    assert any("Condition contradiction" in flag for flag in decision.deal.risk_flags)


def test_consistent_condition_no_contradiction_but_quantity_still_detected():
    c = cand(
        title="Headphones 2-Pack, Like New",
        description="Includes 1 pack. Like new condition.",
        condition="Used - Like New",
        current_price=20.0,
        typical_price=70.0,
    )
    assert detect_condition_contradiction(c) is None
    r = candidate_contradiction(c)
    assert r is not None and r.is_lead


def test_ambiguous_condition_wording_unproven():
    c = cand(title="Mystery Gadget", description="In great shape, works good.",
             condition=None)
    # "works good" is prose, not a grade: no label is guessed.
    assert candidate_condition(c) is None


# --- the detector is product-agnostic -------------------------------------------

def test_detector_has_no_case_specific_strings():
    source = pathlib.Path(
        "sniperplug/services/listing_contradiction.py"
    ).read_text(encoding="utf-8").lower()
    import re

    for banned in ("hotties", "foot", "warmer", "warmers", "zoro"):
        assert not re.search(rf"\b{banned}\b", source), banned
