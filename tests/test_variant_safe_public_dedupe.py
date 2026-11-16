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


def test_walmart_card_builder_carries_variant_identity_to_public_card():
    # The renderer's copy of build_walmart_cards was deleted; the live builder
    # is the deal scanner's. It must still carry variant identity onto the
    # public card so dedupe never collapses distinct variants of one product.
    source = open("sniperplug/cogs/deal_scanner.py", encoding="utf-8").read()
    assert "variant_attributes = _deal_card_variant_attributes(candidate, deal)" in source
    assert "variant_attributes=variant_attributes" in source
    assert "seller_name=_first_present(" in source
    assert "fulfillment_type=_first_present(" in source
    assert "card.selected_offer_id = deal.selected_offer_id" in source
