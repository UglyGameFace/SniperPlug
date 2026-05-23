from sniperplug.cogs.home_depot_search import build_home_depot_card_batch, build_home_depot_cards
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


def test_home_depot_zip_anchor_counts_as_local_proof():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Bathroom Vanity",
        product_url="https://example.com",
        current_price=159.00,
        product_id="203486567",
        sku="203486567",
        signals=["zip: 06610"],
    )

    batch = build_home_depot_card_batch((candidate,), has_local_anchor=True, penny_mode=True)

    assert batch.returned_count == 1
    assert batch.shown_count == 1
    assert batch.used_raw_fallback is False
    assert "No store_id" not in batch.embeds[0].fields[-1].value


def test_home_depot_penny_hunt_falls_back_to_raw_results_when_filter_hides_all():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Regular Full Price Product",
        product_url="https://example.com",
        current_price=199.99,
        product_id="999",
        sku="999",
        signals=[],
    )

    batch = build_home_depot_card_batch((candidate,), has_local_anchor=False, penny_mode=True)

    assert batch.returned_count == 1
    assert batch.shown_count == 1
    assert batch.filtered_count == 1
    assert batch.used_raw_fallback is True
    assert "RAW SERPAPI RESULT" in batch.embeds[0].title
    assert "will not hide paid-credit results" in [field.value for field in batch.embeds[0].fields if field.name == "🟢 Liveness"][0]


def test_home_depot_price_block_shows_typical_price_savings():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Special Buy Vanity",
        product_url="https://example.com",
        current_price=1139.00,
        typical_price=1899.00,
        product_id="327191749",
        sku="327191749",
        signals=["zip: 06610"],
    )

    batch = build_home_depot_card_batch((candidate,), has_local_anchor=True, penny_mode=False)
    price_field = [field.value for field in batch.embeds[0].fields if field.name == "💰 Price"][0]

    assert "Was/typical: **$1,899.00**" in price_field
    assert "Save: **$760.00 (40%)**" in price_field
