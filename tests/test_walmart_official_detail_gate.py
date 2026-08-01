from __future__ import annotations

from pathlib import Path

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _merge_exact_candidate,
    exact_detail_verified_candidates,
)


def candidate(
    item_id: str,
    *,
    current: float = 40.0,
    reference: float | None = None,
    reference_path: str | None = None,
) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        api_reference_path=reference_path,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        seller_name="Walmart",
        condition="New",
        fulfillment_type="shipping",
        variant_attributes={},
    )


def test_numeric_exact_reference_is_trusted_even_without_source_path() -> None:
    original = candidate("111111", current=50.0, reference=90.0, reference_path="search.wasPrice")
    exact = candidate("111111", current=40.0, reference=100.0, reference_path=None)

    merged = _merge_exact_candidate(original, exact, item_id="111111")

    assert merged.api_reference_price == 100.0
    assert merged.typical_price == 100.0
    assert merged.api_discount_percent == 60.0
    assert merged.api_reference_path == "walmart.exact_detail.reference_price"
    assert merged.variant_attributes["referencePriceTrusted"] == "yes"
    assert merged.variant_attributes["exactDetailReferenceStatus"] == "trusted"
    assert merged.variant_attributes["trustedReferencePrice"] == "100.00"


def test_source_path_without_numeric_reference_is_not_trusted() -> None:
    original = candidate("222222", current=50.0, reference=90.0, reference_path="search.wasPrice")
    exact = candidate("222222", current=40.0, reference=None, reference_path="wasPrice")

    merged = _merge_exact_candidate(original, exact, item_id="222222")

    assert merged.api_reference_price is None
    assert merged.typical_price is None
    assert merged.api_discount_percent is None
    assert merged.api_reference_path is None
    assert merged.variant_attributes["referencePriceTrusted"] == "no"
    assert merged.variant_attributes["exactDetailReferenceStatus"] == "missing"


def test_only_exact_detail_verified_candidates_can_be_surfaced() -> None:
    search_only = candidate("333333", current=20.0, reference=80.0, reference_path="search.wasPrice")
    original = candidate("444444", current=20.0, reference=80.0, reference_path="search.wasPrice")
    exact = candidate("444444", current=18.0, reference=75.0, reference_path="wasPrice")
    verified = _merge_exact_candidate(original, exact, item_id="444444")

    assert exact_detail_verified_candidates([search_only, verified]) == [verified]


def test_autoscan_builds_all_card_lanes_from_exact_candidates_only() -> None:
    source = Path("sniperplug/services/autoscan_observed_price_memory.py").read_text(
        encoding="utf-8"
    )

    gate = source.index("exact_candidates = exact_detail_verified_candidates")
    aggregate = source.index("aggregate = ProviderScanResult")
    review = source.index("review_candidates = build_review_candidate_cards")
    scout = source.index("scout_cards = scout_low_price_leads")
    memory = source.index("observed_memory = await select_observed_price_drop_cards")

    assert gate < aggregate < review < scout < memory
    assert "candidates=tuple(exact_candidates)" in source
    assert "build_review_candidate_cards(\n        exact_candidates," in source
    assert "scout_low_price_leads(\n        exact_candidates," in source
    assert "candidates=exact_candidates" in source
