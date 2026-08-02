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


def test_cash_truth_requires_strict_api_proof_positive_amount_and_evidence():
    assert "walmartCashApiProof" in OFFERS_FIND
    assert "strict_api_field_amount" in OFFERS_FIND
    assert "walmartCashAmount" in OFFERS_FIND
    assert "amount <= 0" in OFFERS_FIND
    assert "walmartCashProofPath" in OFFERS_FIND
    assert "walmartCashProofText" in OFFERS_FIND
    assert "return None" in OFFERS_FIND


def test_cash_summary_distinguishes_unavailable_partial_and_checked_zero():
    assert "Proof unavailable" in SUMMARY
    assert "Partial check" in SUMMARY
    assert "No API-proven Walmart Cash in this scan" in SUMMARY
    assert "does **not** prove the Walmart app has no Cash offers" in SUMMARY
    assert "fake zero" in SUMMARY


def test_normal_cash_summary_does_not_dump_other_promo_diagnostics():
    for token in (
        "Other promo types seen separately",
        "Search routes actually checked",
        "Raw proof evidence",
        "pdp_fallback_checked",
        "html_chars",
    ):
        assert token not in SUMMARY


def test_cash_terms_strip_promo_words_and_use_department_defaults():
    assert "DEFAULT_CASH_QUERIES" in TERMS
    assert "walmart\\s+cash" in TERMS
    assert "cash\\s+offers?" in TERMS
    assert "return (cleaned,)" in TERMS
