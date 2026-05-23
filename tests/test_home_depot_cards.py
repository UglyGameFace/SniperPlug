from sniperplug.cogs.home_depot_search import build_home_depot_cards, home_depot_price_block
from sniperplug.models.candidate import SourceCandidate


def test_home_depot_card_uses_sniperplug_deal_format():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Milwaukee Drill Clearance",
        product_url="https://example.com",
        current_price=5.03,
        product_id="1001234567",
        sku="1001234567",
        stock_status="Limited stock",
        signals=["store_id: 6237", "zip: 06610", "clearance price-ending signal: .03"],
    )

    cards = build_home_depot_cards((candidate,), has_store_id=True, penny_mode=True)

    assert len(cards) == 1
    embed = cards[0]
    field_names = [field.name for field in embed.fields]
    assert "💰 Price" in field_names
    assert "📊 Sniper Read" in field_names
    assert "📦 Stock" in field_names
    assert "🟢 Liveness" in field_names
    assert "🔎 Why it showed up" in field_names
    assert "SKU: 1001234567" in embed.footer.text
    assert "Verify in store before posting" in embed.footer.text


def test_home_depot_penny_mode_does_not_hide_weak_paid_results():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Normal Vanity",
        product_url="https://example.com",
        current_price=1139.00,
        product_id="327191749",
        sku="327191749",
        signals=["zip: 06610"],
    )

    cards = build_home_depot_cards((candidate,), has_store_id=False, penny_mode=True)

    assert len(cards) == 1
    assert "HOME DEPOT LEAD" in cards[0].title


def test_home_depot_price_block_shows_typical_price_and_savings():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Special Buy Vanity",
        product_url="https://example.com",
        current_price=1139.00,
        typical_price=1899.00,
    )

    price_block = home_depot_price_block(candidate)

    assert "$1,139.00" in price_block
    assert "$1,899.00" in price_block
    assert "$760.00" in price_block
    assert "40%" in price_block
