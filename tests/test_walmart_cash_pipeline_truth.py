from sniperplug.services.walmart_promo_classifier import classify_walmart_api_promos
from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth
from sniperplug.services.walmart_cash_offers import build_walmart_cash_summary_embed


def test_walmart_cash_with_amount_is_accepted():
    item = {
        "itemId": "123",
        "name": "Detergent",
        "salePrice": 9.99,
        "promotions": [{"type": "WALMART_CASH", "description": "Earn $8 Walmart Cash", "amount": 8}],
    }

    proof = extract_walmart_cash_api_truth(item, current_price=9.99)
    scan = classify_walmart_api_promos(item, current_price=9.99)

    assert proof is not None
    assert proof.amount == 8
    assert scan.cash is not None


def test_walmart_cash_text_without_amount_is_rejected():
    item = {
        "itemId": "123",
        "name": "Baby wipes",
        "salePrice": 12.99,
        "badges": [{"text": "Walmart Cash eligible"}],
    }

    assert extract_walmart_cash_api_truth(item, current_price=12.99) is None
    assert classify_walmart_api_promos(item, current_price=12.99).cash is None


def test_onepay_is_not_walmart_cash():
    item = {
        "itemId": "456",
        "name": "Soap",
        "salePrice": 6.99,
        "onePayCashRewards": "Earn up to 5% cash back with OnePay",
    }

    scan = classify_walmart_api_promos(item, current_price=6.99)

    assert scan.cash is None
    assert scan.onepay is not None


def test_buy_more_save_more_is_cart_promo_not_cash():
    item = {
        "itemId": "789",
        "name": "Toy",
        "salePrice": 18.99,
        "promotions": [{"text": "Buy more, save up to $10 | View eligible items"}],
    }

    scan = classify_walmart_api_promos(item, current_price=18.99)

    assert scan.cash is None
    assert scan.cart_promo is not None
    assert scan.cart_promo.amount == 10


def test_search_only_api_mode_says_proof_unavailable_not_app_has_no_offers():
    embed = build_walmart_cash_summary_embed(
        "detergent",
        ("detergent",),
        9,
        0,
        (),
        detail_checked=0,
        detail_unavailable=True,
        capability_label="Search-only/disabled API access",
    )
    text = "\n".join([embed.description or ""] + [f"{field.name}\n{field.value}" for field in embed.fields])

    assert "Proof unavailable" in text
    assert "missing coverage" in text
    assert "not proof that the Walmart app has no Cash offers" in text


def test_timeout_is_partial_check_not_fake_zero():
    embed = build_walmart_cash_summary_embed(
        "detergent",
        ("detergent",),
        0,
        0,
        ("Timed out checking official Walmart API route `detergent` page 1.",),
        detail_checked=0,
        partial=True,
        capability_label="Signed Affiliate API configured",
    )
    text = "\n".join([embed.description or ""] + [f"{field.name}\n{field.value}" for field in embed.fields])

    assert "Partial check" in text
    assert "fake zero" in text
    assert "official Walmart API" in text


def test_api_probe_builder_exists():
    from sniperplug.services.walmart_cash_offers import build_walmart_api_probe_embed

    assert callable(build_walmart_api_probe_embed)
