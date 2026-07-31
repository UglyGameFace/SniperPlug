from pathlib import Path

from sniperplug.services.walmart_cash import parse_money_amount, walmart_cash_amount_is_sane
from sniperplug.services.walmart_pdp_cash_proof import extract_walmart_cash_from_pdp_html


ROOT = Path(__file__).resolve().parents[1]
PDP_SOURCE = (ROOT / "sniperplug/services/walmart_pdp_cash_proof.py").read_text(encoding="utf-8")


def test_money_parser_is_shared_and_handles_currency_text() -> None:
    assert parse_money_amount("$1,234.56") == 1234.56
    assert parse_money_amount("reward: $15.00") == 15.0
    assert parse_money_amount("no amount") is None


def test_normal_cash_validation_requires_current_price() -> None:
    assert walmart_cash_amount_is_sane(15.0, current_price=None) is False
    assert walmart_cash_amount_is_sane(15.0, current_price=20.0) is True
    assert walmart_cash_amount_is_sane(9999.0, current_price=20.0) is False


def test_exact_pdp_proof_keeps_conservative_missing_price_support() -> None:
    html = "<html><body>Earn $15.00 in Walmart Cash on this item.</body></html>"
    truth = extract_walmart_cash_from_pdp_html(html, current_price=None)
    assert truth is not None
    assert truth.amount == 15.0


def test_pdp_module_does_not_redefine_cash_sanity_or_money_parser() -> None:
    assert "def _amount_is_sane" not in PDP_SOURCE
    assert "def _float_or_none" not in PDP_SOURCE
    assert "walmart_cash_amount_is_sane" in PDP_SOURCE
    assert "parse_money_amount" in PDP_SOURCE
