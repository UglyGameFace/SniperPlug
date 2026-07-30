from sniperplug.services.variant_identity import derived_variant_identity


def test_variant_identity_is_stable_and_order_independent():
    first = derived_variant_identity(
        variant_label="Xbox Series X",
        variant_attributes={"color": "Black", "sellerId": "seller-1"},
        seller_name="Acme",
        fulfillment_type="shipping",
    )
    second = derived_variant_identity(
        variant_label=" xbox  series x ",
        variant_attributes={"sellerId": "SELLER-1", "color": "BLACK"},
        seller_name="ACME",
        fulfillment_type="SHIPPING",
    )
    assert first == second
    assert first and first.startswith("variant:")


def test_platform_and_seller_variants_do_not_collapse():
    xbox = derived_variant_identity(platform="Xbox", seller_name="Seller A")
    playstation = derived_variant_identity(platform="PlayStation", seller_name="Seller A")
    other_seller = derived_variant_identity(platform="Xbox", seller_name="Seller B")
    assert len({xbox, playstation, other_seller}) == 3


def test_no_variant_evidence_returns_none():
    assert derived_variant_identity() is None


def test_walmart_renderer_carries_variant_identity_to_public_card():
    source = open("sniperplug/services/walmart_card_renderer.py", encoding="utf-8").read()
    assert "deal.selected_offer_id or derived_variant_identity(" in source
    assert "card.variant_attributes = dict(deal.variant_attributes or {})" in source
    assert "card.seller_name = deal.seller_name" in source
    assert "card.fulfillment_type = deal.fulfillment_type" in source
