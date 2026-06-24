from __future__ import annotations

from tests.helpers.source_scope import function_source


OFFERS_FIND = function_source(
    "sniperplug/services/walmart_cash_offers.py",
    "find_walmart_cash_offer",
)

SUMMARY = function_source(
    "sniperplug/services/walmart_cash_offers.py",
    "build_walmart_cash_summary_embed",
)

TERMS = function_source(
    "sniperplug/services/walmart_cash_offers.py",
    "walmart_cash_search_terms",
)


def test_cash_truth_requires_api_proof_and_amount():
    assert "walmartCashApiProof" in OFFERS_FIND
    assert "strict_api_field_amount" in OFFERS_FIND
    assert "walmartCashAmount" in OFFERS_FIND
    assert "amount <= 0" in OFFERS_FIND
    assert "return None" in OFFERS_FIND


def test_cash_summary_distinguishes_unavailable_from_zero():
    assert "Walmart did not expose full promo detail through the current API access" in SUMMARY
    assert "Proof unavailable" in SUMMARY
    assert "No API-proven Walmart Cash found in checked detail rows" in SUMMARY
    assert "Partial check" in SUMMARY


def test_cash_summary_separates_other_promo_types():
    assert "cart_promo" in SUMMARY
    assert "onepay" in SUMMARY
    assert "markdown" in SUMMARY
    assert "clearance" in SUMMARY
    assert "generic_promo" in SUMMARY


def test_cash_terms_are_bounded_and_relevant():
    assert "DEFAULT_CASH_QUERIES" in TERMS
    assert "walmart cash offers" in TERMS
    assert "walmart cash eligible" in TERMS
