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
