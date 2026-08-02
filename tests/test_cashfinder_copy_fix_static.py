from pathlib import Path

OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def test_cashfinder_normal_copy_is_compact_truthful_and_api_only():
    assert "Walmart Cash feed unavailable" in OFFERS
    assert "not a supported Walmart Cash offer feed" in OFFERS
    assert "No ordinary product searches or detail probes are run" in OFFERS
    assert "Open Walmart's official Manufacturer Offers catalog" in OFFERS
    assert "No public PDP scraping or Robot/Human-page probing" in OFFERS
    assert "API-proven Cash links" in OFFERS
    assert "Search routes actually checked" not in OFFERS
    assert "Raw proof evidence" not in OFFERS
