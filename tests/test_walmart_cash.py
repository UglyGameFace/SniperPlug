from sniperplug.services.walmart_cash import strict_walmart_promotion_proof


def test_rejects_impossible_walmart_cash_and_coupon_values():
    attrs = strict_walmart_promotion_proof(
        {
            "promo": {"coupon": "$25000.00"},
            "walmartCashOffer": {"name": "Walmart Cash", "amount": 25000},
        },
        current_price=9.99,
        coupon_amount=25000,
    )

    assert attrs == {}
