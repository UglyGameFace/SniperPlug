from sniperplug.cogs.home_depot_search import build_home_depot_cards
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
