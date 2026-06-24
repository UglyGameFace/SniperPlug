from pathlib import Path


DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
CASH = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
TRUTH = Path("sniperplug/services/walmart_cash_api_truth.py").read_text(encoding="utf-8")


def test_walmart_cash_routes_run_concurrently():
    assert "asyncio.gather" in DEALS
    assert "asyncio.Semaphore(4)" in DEALS
    assert "asyncio.wait_for" in DEALS
    assert "timeout=18" in DEALS


def test_cash_cards_require_strict_amount_proof_mode():
    assert "strict_api_field_amount" in CASH
    assert "walmartCashProofMode" in CASH
    assert "sane dollar amount" in CASH


def test_truth_layer_blocks_common_false_positive_sources():
    assert "REJECT_CONTEXT_MARKERS" in TRUTH
    assert "buy more" in TRUTH
    assert "view eligible items" in TRUTH
    assert "walmartCashOffer" not in TRUTH  # no hardcoded one-off field dependency
