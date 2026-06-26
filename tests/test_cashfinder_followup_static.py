from pathlib import Path

OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def test_cashfinder_followup_truth_copy_is_preserved():
    assert "This is **not** a proven no-offer result" in OFFERS
    assert "Walmart API timed out before product data returned" in OFFERS
    assert "No API-confirmed Cash Offers found in checked products" in OFFERS
    assert "Cash Finder does not public-post markdown/open-box alerts" in OFFERS
