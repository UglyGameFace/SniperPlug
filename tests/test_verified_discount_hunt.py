from sniperplug.services.verified_discount_hunt import (
    ALL_VERIFIED_HUNT_KEY,
    ALL_VERIFIED_PRESET,
    DISCOVERY_QUERIES,
    PAGES_PER_QUERY,
    RESULTS_PER_PAGE,
    SORT_PASSES,
    TRUE_DISCOUNT_MIN,
)


def test_verified_hunt_minimum_discount_is_fifty():
    assert TRUE_DISCOUNT_MIN == 50
    assert ALL_VERIFIED_PRESET.min_discount == 50


def test_verified_hunt_single_button_key():
    assert ALL_VERIFIED_PRESET.key == ALL_VERIFIED_HUNT_KEY


def test_verified_hunt_uses_broad_walmart_routes():
    terms = " ".join(DISCOVERY_QUERIES).lower()
    assert len(DISCOVERY_QUERIES) >= 20
    assert "clearance" in terms
    assert "rollback" in terms
    assert "restored" in terms


def test_verified_hunt_uses_full_pages_and_sort_passes():
    assert RESULTS_PER_PAGE == 25
    assert PAGES_PER_QUERY >= 5
    assert ("price", "ascending") in SORT_PASSES
