from sniperplug.cogs.home_depot_local import build_hd_penny_zip_embed, build_hd_stock_embed, HomeDepotLocalScan
from sniperplug.models.candidate import SourceCandidate


def test_hd_stock_embed_shows_local_proof_warning_and_product_data():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="SteamSpot Corded Steam Mop",
        product_url="https://www.homedepot.com/p/334851114",
        current_price=71.00,
        typical_price=129.99,
        product_id="334851114",
        sku="334851114",
        stock_status="Store stock: 19 (In Stock)",
        variant_attributes={"pickup": "Pickup: available", "store_stock": "19"},
        signals=["zip: 06610", "SerpApi Home Depot search result; not an in-store scan confirmation"],
    )
    scan = HomeDepotLocalScan(
        sku="334851114",
        zip_code="06610",
        query="334851114",
        candidates=(candidate,),
        warnings=(),
        quota_text="SerpApi used: 1/10 today",
    )

    embed = build_hd_stock_embed(scan)
    fields = {field.name: field.value for field in embed.fields}

    assert "Home Depot Stock Check" in embed.title
    assert "334851114" in fields["Product"]
    assert "Now: **$71.00**" in fields["Price"]
    assert "Was: **$129.99**" in fields["Price"]
    assert "Store stock: 19" in fields["Stock / fulfillment"]
    assert "Public alert: **No" in fields["SniperPlug read"]
    assert "Call/check before driving" in embed.footer.text


def test_hd_penny_zip_embed_lists_ranked_candidates():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Clearance Faucet",
        product_url="https://www.homedepot.com/p/123456789",
        current_price=5.03,
        product_id="123456789",
        sku="123456789",
        signals=["clearance price-ending signal: .03"],
    )

    embed = build_hd_penny_zip_embed("06610", (candidate,), (), used=2, limit=10)
    fields = {field.name: field.value for field in embed.fields}

    assert "06610" in embed.title
    assert "Top candidates" in fields
    assert "Clearance Faucet" in fields["Top candidates"]
    assert "SerpApi used: 2/10 today" in embed.footer.text
