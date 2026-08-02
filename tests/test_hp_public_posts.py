from __future__ import annotations

from copy import deepcopy

from sniperplug.hp_watcher.parser import HPPriceOffer
from sniperplug.hp_watcher.service import _candidate_for_hp_offer
from sniperplug.hp_watcher.storage import CatalogProduct, OfferDecision
from sniperplug.services.hp_deal_cards import build_hp_deal_card
from sniperplug.services.hp_public_posts import is_verified_hp_public_card


PRODUCT = CatalogProduct(
    product_key="hp-product:test",
    product_url="https://www.hp.com/us-en/shop/pdp/hp-travel-25-liter-156-iron-grey-laptop-backpack",
    sku="6B8U4AA",
    catalog_entry_id="3074457345619999999",
    title="HP Travel 25 Liter 15.6 Iron Grey Laptop Backpack",
    image_url="https://example.hp.com/backpack.png",
)
OFFER = HPPriceOffer(
    product_id=PRODUCT.catalog_entry_id,
    part_number="6B8U4AA#ABA",
    sku=PRODUCT.sku,
    current_price=12.99,
    msrp_price=54.99,
    promotion_text="Clearance",
    in_stock=True,
    can_add_to_cart=True,
)
DECISION = OfferDecision(
    product_key=PRODUCT.product_key,
    event_key="hp-event:v1:test",
    event_type="msrp_markdown",
    should_publish=True,
    current_price=12.99,
    reference_price=54.99,
    reference_source="hp.services.priceData.lPrice.msrp",
    discount_percent=76.38,
)


def verified_card():
    return build_hp_deal_card(
        _candidate_for_hp_offer(PRODUCT, OFFER, DECISION),
        event_key=DECISION.event_key,
    )


def test_exact_hp_card_passes_hp_specific_public_gate() -> None:
    card = verified_card()
    assert is_verified_hp_public_card(card, min_discount=50) is True
    assert card.public_post_key == DECISION.event_key
    assert card.selected_offer_id == f"hp:{PRODUCT.catalog_entry_id}:{PRODUCT.sku}"
    assert card.api_current_price == 12.99
    assert card.api_reference_price == 54.99
    assert card.variant_attributes["hpIndependentConfirmation"] == "yes"


def test_hp_public_gate_rejects_wrong_domain_and_offer_identity() -> None:
    wrong_domain = verified_card()
    wrong_domain.direct_product_url = "https://example.com/fake"
    wrong_domain.url = "https://example.com/fake"
    assert is_verified_hp_public_card(wrong_domain, min_discount=10) is False

    mismatched_offer = verified_card()
    mismatched_offer.selected_offer_id = "hp:wrong:identity"
    assert is_verified_hp_public_card(mismatched_offer, min_discount=10) is False


def test_hp_public_gate_rejects_zero_price_and_untrusted_reference() -> None:
    zero = verified_card()
    zero.current_price = 0.0
    zero.api_current_price = 0.0
    assert is_verified_hp_public_card(zero, min_discount=10) is False

    untrusted = verified_card()
    untrusted.variant_attributes = deepcopy(untrusted.variant_attributes)
    untrusted.variant_attributes["trustedReferencePrice"] = "999.99"
    assert is_verified_hp_public_card(untrusted, min_discount=10) is False


def test_hp_public_gate_rejects_unconfirmed_structured_price() -> None:
    card = verified_card()
    card.variant_attributes = deepcopy(card.variant_attributes)
    card.variant_attributes.pop("hpIndependentConfirmation")
    assert is_verified_hp_public_card(card, min_discount=10) is False


def test_hp_public_gate_honors_each_server_threshold() -> None:
    card = verified_card()
    assert is_verified_hp_public_card(card, min_discount=75) is True
    assert is_verified_hp_public_card(card, min_discount=80) is False
