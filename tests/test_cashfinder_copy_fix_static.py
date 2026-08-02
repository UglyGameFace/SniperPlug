from pathlib import Path

OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def test_cashfinder_normal_copy_is_compact_truthful_and_api_only():
    assert "No API-proven Walmart Cash in this scan" in OFFERS
    assert "This does not prove the Walmart app has no Cash offers" in OFFERS
    assert "Official Walmart API only" in OFFERS
    assert "No public PDP scraping or Robot/Human-page probing" in OFFERS
    assert "Unconfirmed badges are hidden until an exact dollar amount is returned" in OFFERS
    assert "API-proven Cash links" in OFFERS
    assert "Search routes actually checked" not in OFFERS
    assert "Raw proof evidence" not in OFFERS
