from sniperplug.models.deal import NormalizedDeal
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_card_renderer import api_detail_lines


def test_api_detail_lines_show_key_walmart_proof():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Test item",
        product_url="https://www.walmart.com/ip/123",
        current_price=10,
        typical_price=20,
        sku="123",
        upc="999",
        seller_name="Walmart",
        fulfillment_type="Shipping",
        condition="New",
        variant_label="Blue / 2 pack",
        variant_attributes={
            "walmartSeller": "yes",
            "rollback": "yes",
            "availableOnline": "yes",
            "brand": "Acme",
            "modelNumber": "ABC-1",
            "maxOrderQty": "2",
            "referencePriceTrusted": "yes",
            "couponSavings": "3.00",
            "walmartCashSavings": "2.00",
        },
    )
    deal = candidate.to_normalized_deal()

    lines = api_detail_lines(candidate, deal)
    rendered = "\n".join(lines)

    assert "SKU `123`" in rendered
    assert "UPC `999`" in rendered
    assert "Seller **Walmart**" in rendered
    assert "Rollback: **yes**" in rendered
    assert "Reference trusted: **yes**" in rendered
    assert "Coupon API value: **$3.00**" in rendered
    assert "Walmart Cash API value: **$2.00**" in rendered
