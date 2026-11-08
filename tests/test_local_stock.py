from sniperplug.models.local_stock import LocalStockOffer, StoreLocation

# NOTE (C6): the score tests were deleted with
# sniperplug/services/local_stock_score.py (zero runtime importers). The
# live models/local_stock.py offer -> candidate conversion is still covered
# below.


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
