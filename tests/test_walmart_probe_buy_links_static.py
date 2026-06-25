from pathlib import Path


OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def test_probe_says_clearance_signals_are_not_buy_worthy_links():
    assert "not a shopping list" in OFFERS
    assert "promo signals only" in OFFERS
    assert "A clearance flag by itself does not prove a discount" in OFFERS
    assert "Direct product links only show for API-proven Cash candidates" in OFFERS


def test_probe_only_builds_links_from_cash_candidates():
    helper = OFFERS[OFFERS.index("def build_walmart_api_probe_embed"):]
    assert "cash_candidates = tuple" in helper
    assert "API-proven Cash links" in helper
    assert "cash_candidates[:5]" in helper
    assert "product_url" in helper
