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

    assert score.score >= 85
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

    assert score.score < 25
    assert score.level == "low_confidence_result"
    assert any("No store_id or ZIP" in reason for reason in score.reasons)


def test_dot_zero_zero_vanity_is_not_clearance_by_itself():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Doveton 48 in. Vanity",
        product_url="https://example.com",
        current_price=629.00,
        typical_price=699.00,
        product_id="324252762",
        sku="324252762",
        signals=["zip: 06610"],
    )

    score = score_penny_candidate(candidate, has_store_id=True)

    assert score.score < 25
    assert score.level == "low_confidence_result"
    assert any(".00" in reason and "neutral" in reason for reason in score.reasons)


def test_strong_discount_without_markdown_ending_is_markdown_watch_not_clearance():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Bathroom vanity Special Buy",
        product_url="https://example.com",
        current_price=1139.00,
        typical_price=1899.00,
        product_id="327191749",
        sku="327191749",
        variant_attributes={"price_badge": "Special Buy", "percentage_off": "40%"},
        signals=["zip: 06610"],
    )

    score = score_penny_candidate(candidate, has_store_id=True)

    assert 25 <= score.score < 45
    assert score.level == "markdown_watch"


def test_deep_markdown_ending_gets_clearance_candidate():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Bathroom faucet",
        product_url="https://example.com",
        current_price=24.03,
        product_id="123",
        sku="123",
        signals=["zip: 06610"],
    )

    score = score_penny_candidate(candidate, has_store_id=True)

    assert score.score >= 45
    assert score.level in {"clearance_candidate", "strong_penny_candidate", "high_priority_in_store_verification"}
