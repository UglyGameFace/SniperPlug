from pathlib import Path

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_review_candidates import build_review_candidate_cards, is_fragrance_or_beauty, safe_markdown_signal


def test_fragrance_detector_keeps_dolce_route_recognizable():
    assert is_fragrance_or_beauty("Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz") is True


def test_safe_markdown_signal_rejects_unrelated_generic_product_without_signal():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Generic Storage Bin",
        product_url="https://www.walmart.com/ip/2",
        current_price=12.99,
        variant_attributes={"finderSourceQuery": "storage clearance"},
    )

    assert safe_markdown_signal(candidate) is False


def test_review_candidates_keep_api_markdown_leads_without_removed_module():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dolce Gabbana The One Men Eau De Parfum Spray 5.0 oz",
        product_url="https://www.walmart.com/ip/3",
        current_price=65.04,
        sku="123",
        signals=("clearance",),
        variant_attributes={"finderSourceQuery": "dolce perfume clearance"},
    )

    result = build_review_candidate_cards([candidate], limit=5)

    assert len(result.cards) == 1
    rendered = str(result.cards[0].embed.to_dict())
    assert "Review" in rendered
    assert "not a verified 50% deal" in rendered


def test_raw_price_compat_module_stays_removed():
    assert not Path("sniperplug/services/raw_price_review_patch.py").exists()
