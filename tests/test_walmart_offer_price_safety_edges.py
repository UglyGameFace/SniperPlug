from __future__ import annotations

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.walmart_card_renderer import fulfillment_lines, price_lines
from sniperplug.services.walmart_marketplace_comp import (
    selected_offer_delivery_attributes,
)


def test_non_walmart_current_price_is_not_called_delivered_total() -> None:
    candidate = SourceCandidate(
        source_key="target",
        retailer="Target",
        title="Target item",
        product_url="https://www.target.com/p/example/-/A-12345678",
        current_price=19.99,
    )

    assert candidate.item_price == 19.99
    assert candidate.delivered_price is None
    assert candidate.shipping_cost is None
    assert candidate.shipping_status is None

    deal = candidate.to_normalized_deal()
    assert deal.item_price == 19.99
    assert deal.delivered_price is None
    assert deal.availability_message is not None
    assert "Item price: $19.99" in deal.availability_message
    assert "Delivered total" not in deal.availability_message


def test_selected_offer_unit_price_dict_is_not_used_as_offer_total() -> None:
    attrs = selected_offer_delivery_attributes(
        {
            "selectedOffer": {
                "offerId": "unit-price-offer",
                "sellerId": "seller-unit",
                "sellerName": "Unit Price Seller",
                "price": {"price": 0.12, "unit": "oz"},
                "shippingCost": 0.00,
                "isMarketPlaceItem": True,
            }
        }
    )

    assert "selectedOfferItemPrice" not in attrs
    assert "selectedOfferDeliveredPrice" not in attrs
    assert "selectedOfferShippingCost" not in attrs


def test_currency_unit_dict_remains_valid_offer_price() -> None:
    attrs = selected_offer_delivery_attributes(
        {
            "selectedOffer": {
                "offerId": "currency-price-offer",
                "sellerId": "seller-currency",
                "sellerName": "Currency Seller",
                "currentPrice": {"amount": 20.00, "unit": "USD"},
                "shippingCost": 5.00,
                "isMarketPlaceItem": True,
            }
        }
    )

    assert attrs["selectedOfferItemPrice"] == "20.00"
    assert attrs["selectedOfferShippingCost"] == "5.00"
    assert attrs["selectedOfferDeliveredPrice"] == "25.00"


def test_zero_value_offer_evidence_is_rendered_without_falsy_fallback() -> None:
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Free promotional item",
        product_url="https://www.walmart.com/ip/123456",
        current_price=0.00,
        item_price=0.00,
        shipping_cost=0.00,
        delivered_price=0.00,
        shipping_status="free",
        shipping_source="test.shipping",
        variant_attributes={
            "itemPrice": "9.99",
            "deliveredPrice": "9.99",
            "shippingCost": "0.00",
            "shippingStatus": "free",
            "selectedOfferItemPriceSource": "test.item",
        },
    )
    deal = NormalizedDeal(
        title="Free promotional item",
        retailer="Walmart",
        product_url="https://www.walmart.com/ip/123456",
        current_price=0.00,
        item_price=0.00,
        shipping_cost=0.00,
        delivered_price=0.00,
        shipping_status="free",
        shipping_source="test.shipping",
        variant_attributes=dict(candidate.variant_attributes),
    )
    proof = verified_deal_value(deal)

    rendered_price = "\n".join(price_lines(candidate, deal, proof))
    rendered_fulfillment = "\n".join(fulfillment_lines(candidate, deal))

    assert "Selected-offer item price: **$0.00**" in rendered_price
    assert "Delivered total used for deal math: **$0.00**" in rendered_price
    assert "Selected item price: **$0.00**" in rendered_fulfillment
    assert "Delivered total: **$0.00**" in rendered_fulfillment
    assert "$9.99" not in rendered_price
    assert "$9.99" not in rendered_fulfillment
