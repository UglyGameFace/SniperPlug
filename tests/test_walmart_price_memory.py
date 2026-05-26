from sniperplug.services.walmart_price_memory import PriceMemoryDecision, memory_identity


class DummyCard:
    retailer = "Walmart"
    selected_offer_id = "offer-1"
    sku = "sku-1"
    upc = "upc-1"
    url = "https://www.walmart.com/ip/123?x=1"


def test_memory_identity_prefers_offer_id():
    assert memory_identity(DummyCard(), retailer="walmart") == "walmart:offer-1"


def test_memory_decision_show_statuses():
    show_statuses = {"new", "lower_price", "new_low", "better_value", "offer_changed", "restocked"}
    for status in show_statuses:
        assert PriceMemoryDecision(card=object(), status=status, reason="x").should_show is True


def test_memory_decision_hides_same_or_higher_price():
    assert PriceMemoryDecision(card=object(), status="same_or_higher", reason="x").should_show is False
    assert PriceMemoryDecision(card=object(), status="unknown_price", reason="x").should_show is False
