from pathlib import Path

OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def test_cashfinder_zero_result_and_probe_copy_stays_compatible():
    assert "This is **not** a proven no-offer result" in OFFERS
    assert "No API-proven Walmart Cash found in checked detail rows" in OFFERS
    assert "not proof that no Walmart Cash offers exist" in OFFERS
    assert "Direct product links only show for API-proven Cash candidates" in OFFERS
    assert "API-proven Cash links" in OFFERS
