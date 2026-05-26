from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.walmart_card_renderer import build_deal_card_embed, evidence_lines, price_lines


def test_price_lines_label_reference_context_without_counting_discount():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="API item",
        product_url="https://www.walmart.com/ip/1",
        current_price=10.0,
        typical_price=None,
        variant_attributes={"referenceContextPrice": "20.00", "referenceContextSource": "listPrice"},
    )
    deal = candidate.to_normalized_deal()
    rendered = "\n".join(price_lines(candidate, deal, verified_deal_value(deal)))
    assert "Reference shown: **$20.00** `listPrice`" in rendered
    assert "reference shown but not counted" in rendered
    assert "API-derived savings" not in rendered


def test_evidence_lines_show_walmart_source_signals():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="API item",
        product_url="https://www.walmart.com/ip/2",
        current_price=10.0,
        typical_price=20.0,
        signals=["Walmart current price source: salePrice", "Walmart reference price source: wasPrice"],
    )
    deal = candidate.to_normalized_deal()
    rendered = "\n".join(evidence_lines(candidate, deal, verified_deal_value(deal)))
    assert "salePrice" in rendered
    assert "wasPrice" in rendered


def test_card_has_full_api_sections():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Complete API item",
        product_url="https://www.walmart.com/ip/3",
        current_price=25.0,
        typical_price=50.0,
        sku="3",
        upc="123456789",
        selected_offer_id="offer-1",
        variant_label="Blue / 2 pack",
        variant_attributes={
            "trustedReferencePrice": "50.00",
            "trustedReferenceSource": "wasPrice",
            "seller": "Walmart",
            "walmartSeller": "yes",
            "availableOnline": "yes",
            "shipToStore": "no",
            "brand": "Acme",
            "modelNumber": "ABC",
            "packSize": "2 pack",
            "color": "Blue",
        },
        stock_status="Available",
        can_add_to_cart=True,
        seller_name="Walmart",
        fulfillment_type="Shipping",
        condition="New",
        signals=["Walmart current price source: salePrice", "Walmart reference price source: wasPrice"],
    )
    deal = candidate.to_normalized_deal()
    rendered = str(build_deal_card_embed(candidate, deal, verified_deal_value(deal)).to_dict())
    assert "Price from Walmart API" in rendered
    assert "Product identity from API" in rendered
    assert "Offer / seller from API" in rendered
    assert "Fulfillment / stock from API" in rendered
    assert "Variant / option from API" in rendered
    assert "No guessed card values" in rendered
