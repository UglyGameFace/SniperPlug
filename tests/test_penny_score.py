from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.penny_score import score_penny_candidate


def test_penny_score_high_priority_for_one_cent_local_candidate():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Milwaukee tool clearance",
        product_url="https://example.com",
        current_price=0.01,
        product_id="123",
        sku="123",
        signals=["clearance"],
    )

    score = score_penny_candidate(candidate, has_store_id=True)

    assert score.score >= 80
    assert score.level == "high_priority_in_store_verification"
    assert any(".01" in reason for reason in score.reasons)
    assert any("Local store/ZIP search" in reason for reason in score.reasons)


def test_penny_score_penalizes_missing_store_and_price():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Generic product",
        product_url="https://example.com",
    )

    score = score_penny_candidate(candidate, has_store_id=False)

    assert score.score < 30
    assert score.level == "weak_lead"
    assert any("No store_id or ZIP" in reason for reason in score.reasons)


def test_penny_score_does_not_treat_dot_zero_zero_discount_as_clearance_watch():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Doveton vanity special buy",
        product_url="https://example.com",
        current_price=629.00,
        typical_price=699.00,
        product_id="324252762",
        sku="324252762",
        signals=["zip: 06610"],
    )

    score = score_penny_candidate(candidate, has_store_id=True)

    assert score.score < 40
    assert score.level == "deal_watch"
    assert any("Normal/common price ending .00" in reason for reason in score.reasons)
    assert not any("Price ending .00: +5" in reason for reason in score.reasons)


def test_penny_score_requires_clearance_or_big_markdown_for_clearance_watch():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Vanity clearance discontinued",
        product_url="https://example.com",
        current_price=629.00,
        typical_price=1299.00,
        product_id="324252762",
        sku="324252762",
        signals=["zip: 06610", "clearance"],
    )

    score = score_penny_candidate(candidate, has_store_id=True)

    assert score.score >= 60
    assert score.level in {"strong_clearance_candidate", "high_priority_in_store_verification"}
    assert any("Explicit clearance" in reason for reason in score.reasons)
    assert any("Large markdown" in reason or "Extreme markdown" in reason for reason in score.reasons)
