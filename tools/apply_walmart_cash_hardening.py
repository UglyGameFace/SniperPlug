from __future__ import annotations

from pathlib import Path


TEST_CONTENT = '''from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth
from sniperplug.services.walmart_cash_offers import walmart_cash_search_terms
from sniperplug.services.walmart_pdp_cash_proof import extract_walmart_cash_from_pdp_html


def test_walmart_cash_object_does_not_treat_price_or_item_id_as_cash_amount():
    item = {
        "itemId": 987654321,
        "price": 29.97,
        "walmartCash": {
            "eligible": True,
            "itemId": 987654321,
            "currentPrice": 29.97,
        },
    }
    assert extract_walmart_cash_api_truth(item, current_price=29.97) is None


def test_dedicated_walmart_cash_amount_field_is_accepted():
    item = {
        "itemId": "123",
        "manufacturerOffer": {
            "label": "Get Walmart Cash",
            "walmartCashAmount": 5.00,
        },
    }
    proof = extract_walmart_cash_api_truth(item, current_price=20.00)
    assert proof is not None
    assert proof.amount == 5.00


def test_exact_action_text_with_amount_is_accepted_but_nearby_price_is_not():
    valid = {"promotion": {"text": "Get $4.00 Walmart Cash after purchase"}}
    invalid = {"promotion": {"text": "Walmart Cash available", "currentPrice": "$24.98"}}
    assert extract_walmart_cash_api_truth(valid, current_price=24.98).amount == 4.00
    assert extract_walmart_cash_api_truth(invalid, current_price=24.98) is None


def test_pdp_requires_cash_action_amount_not_any_nearby_dollar_value():
    valid_html = '<html><body><label>Get $3.00 Walmart Cash</label></body></html>'
    invalid_html = '<html><body><h1>Walmart Cash</h1><span>Price $18.97</span></body></html>'
    assert extract_walmart_cash_from_pdp_html(valid_html, current_price=18.97).amount == 3.00
    assert extract_walmart_cash_from_pdp_html(invalid_html, current_price=18.97) is None


def test_default_discovery_uses_official_manufacturer_offer_language():
    terms = walmart_cash_search_terms(None)
    assert terms[0] == "manufacturer offers"
    assert "get walmart cash" in terms
'''


def require(path: str, snippets: tuple[str, ...]) -> None:
    text = Path(path).read_text(encoding="utf-8")
    missing = [snippet for snippet in snippets if snippet not in text]
    if missing:
        raise SystemExit(f"{path} is missing hardened Walmart Cash logic: {missing}")


def main() -> None:
    require(
        "sniperplug/services/walmart_cash_api_truth.py",
        (
            "WALMART_CASH_AMOUNT_KEY_MARKERS = (",
            "if not _is_dedicated_cash_amount_key(key_norm):",
            "leaf_key = _norm(path.rsplit",
            'r"(?:get|earn|receive|claim)\\s+\\$\\s*',
            'r"\\$\\s*(\\d+(?:\\.\\d{1,2})?)\\s+walmart\\s+cash"',
        ),
    )
    require(
        "sniperplug/services/walmart_pdp_cash_proof.py",
        (
            'r"(?:earn|get|receive|claim)\\s+\\$?\\s*',
            'r"\\$\\s*(\\d+(?:\\.\\d{1,2})?)\\s+walmart\\s+cash"',
        ),
    )
    require(
        "sniperplug/services/walmart_cash_offers.py",
        (
            '"manufacturer offers",',
            '"get walmart cash",',
        ),
    )

    for path in (
        "sniperplug/services/walmart_cash_api_truth.py",
        "sniperplug/services/walmart_pdp_cash_proof.py",
    ):
        text = Path(path).read_text(encoding="utf-8")
        if 'r"walmart\\s+cash[^' in text:
            raise SystemExit(f"unsafe reverse Walmart Cash amount matcher remains in {path}")

    pdp_text = Path("sniperplug/services/walmart_pdp_cash_proof.py").read_text(encoding="utf-8")
    if "nearby_money =" in pdp_text or "abs(match.start() - cash_index) <= 90" in pdp_text:
        raise SystemExit("unsafe PDP nearest-dollar shortcut remains")

    Path("tests/test_walmart_cash_proven_filter.py").write_text(TEST_CONTENT, encoding="utf-8")


if __name__ == "__main__":
    main()
