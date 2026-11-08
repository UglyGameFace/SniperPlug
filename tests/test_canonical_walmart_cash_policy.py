from sniperplug.services.walmart_cash import parse_money_amount, walmart_cash_amount_is_sane


def test_money_parser_is_shared_and_handles_currency_text() -> None:
    assert parse_money_amount("$1,234.56") == 1234.56
    assert parse_money_amount("reward: $15.00") == 15.0
    assert parse_money_amount("no amount") is None


def test_normal_cash_validation_requires_current_price() -> None:
    assert walmart_cash_amount_is_sane(15.0, current_price=None) is False
    assert walmart_cash_amount_is_sane(15.0, current_price=20.0) is True
    assert walmart_cash_amount_is_sane(9999.0, current_price=20.0) is False

# NOTE (C6): test_exact_pdp_proof_keeps_conservative_missing_price_support
# and test_pdp_module_does_not_redefine_cash_sanity_or_money_parser were
# deleted with sniperplug/services/walmart_pdp_cash_proof.py (zero runtime
# importers). Cash validation itself is still covered above via the live
# sniperplug/services/walmart_cash.py module.
