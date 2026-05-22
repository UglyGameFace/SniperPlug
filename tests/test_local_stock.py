from sniperplug.models.local_stock import LocalStockOffer, StoreLocation
from sniperplug.services.local_stock_score import score_local_stock_offer


def test_local_stock_offer_converts_to_source_candidate_with_store_signals():
    offer = LocalStockOffer(
        retailer="Home Depot",
        title="117 oz. HE Ultra Oxi Original Scent Liquid Laundry Detergent",
        product_url="https://www.homedepot.com/p/example/123",
        sku="334771952",
        image_url="https://example.com/tide.jpg",
        local_price=12.00,
        regular_price=22.48,
        stock_quantity=7,
        stock_status="In stock",
        aisle="01",
        bay="6",
        zip_code="06610",
        store=StoreLocation(
            retailer="Home Depot",
            store_id="6201",
            name="North Haven",
            address="111 Universal Drive N",
            city="North Haven",
            state="CT",
            postal_code="06473",
            distance_miles=18.7,
        ),
        source_key="home_depot_local",
        signals=["clearance"],
    )

    candidate = offer.to_source_candidate()

    assert candidate.retailer == "Home Depot"
    assert candidate.current_price == 12.00
    assert candidate.typical_price == 22.48
    assert candidate.sku == "334771952"
    assert candidate.can_add_to_cart is True
    assert "Local stock quantity: 7" in candidate.signals
    assert "Store: North Haven — North Haven, CT" in candidate.signals
    assert "Store location: 01 / 6" in candidate.signals


def test_local_stock_score_rewards_real_local_proof():
    offer = LocalStockOffer(
        retailer="Home Depot",
        title="Nest Doorbell",
        product_url="https://www.homedepot.com/p/example/456",
        sku="123456789",
        local_price=17.00,
        regular_price=179.99,
        stock_quantity=29,
        stock_status="In stock",
        aisle="01",
        bay="18",
        store=StoreLocation(
            retailer="Home Depot",
            store_id="1111",
            name="Smithtown",
            city="Commack",
            state="NY",
            distance_miles=27.8,
        ),
    )

    score = score_local_stock_offer(offer)

    assert score.level in {"strong_local", "hot_local"}
    assert score.score >= 80
    assert "90%+ local markdown" in score.reasons
    assert "Strong local quantity available" in score.reasons
    assert "Store aisle/location proof available" in score.reasons


def test_local_stock_score_penalizes_no_stock():
    offer = LocalStockOffer(
        retailer="Example Store",
        title="Dead local deal",
        product_url="https://example.com/dead",
        local_price=1.00,
        regular_price=100.00,
        stock_quantity=0,
        stock_status="Out of stock",
    )

    score = score_local_stock_offer(offer)

    assert "No local stock quantity reported" in score.reasons
    assert score.score < 110
