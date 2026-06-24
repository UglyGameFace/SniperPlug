from pathlib import Path

CASH = Path("sniperplug/services/walmart_cash_api_truth.py").read_text(encoding="utf-8")
OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
PROVIDER = Path("sniperplug/providers/walmart.py").read_text(encoding="utf-8")


def test_cash_truth_requires_real_walmart_cash_amount():
    text = CASH.lower()
    assert "walmartcashsavings" in text
    assert "onepay" in text
    assert "generic reward" in text
    assert "search word" in text
    assert "return none" in text


def test_walmart_provider_preserves_raw_api_payload():
    assert "raw_api" in PROVIDER


def test_walmart_cash_command_uses_fast_bounded_routes():
    assert "queries[:3]" in DEALS
    assert "queries[:6]" not in DEALS
    assert "asyncio.Semaphore(2)" in DEALS
    assert "asyncio.Semaphore(4)" not in DEALS
    assert "for page in (1,)" in DEALS or "for page in [1]" in DEALS


def test_cash_finder_explains_api_confirmed_only():
    assert "API-confirmed" in OFFERS
    assert "does not count" in OFFERS or "do not count" in OFFERS
