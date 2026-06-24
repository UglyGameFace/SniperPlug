from pathlib import Path


PROVIDER = Path("sniperplug/providers/walmart.py").read_text(encoding="utf-8")
CASH = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")


def test_provider_preserves_raw_walmart_cash_api_proof():
    assert "extract_walmart_cash_api_truth" in PROVIDER
    assert "cash_api_truth.as_attributes()" in PROVIDER
    assert "cash_api_truth.signal()" in PROVIDER


def test_cash_embed_explains_proof_for_normal_users():
    assert "API-confirmed Walmart Cash" in CASH
    assert "API field:" in CASH
    assert "OnePay cashback" in CASH
    assert "app-only screenshots do not count" in CASH
    assert "No API-confirmed Cash Offers found" in CASH


def test_walmart_cash_command_checks_multiple_routes_and_pages():
    assert "for query in queries[:6]" in DEALS
    assert "for page in (1, 2)" in DEALS
    assert 'name="walmart_cash"' in DEALS
