from __future__ import annotations

from copy import deepcopy

from sniperplug.services.target_deal_cards import build_target_deal_card
from sniperplug.services.target_locations import TargetLocationContext
from sniperplug.services.target_public_posts import is_verified_target_public_card
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.parser import TargetOffer
from sniperplug.target_watcher.production_service import candidate_for_target_offer
from sniperplug.target_watcher.storage import TargetCatalogProduct, TargetOfferDecision


TCIN = "91234567"
LOCATION = TargetLocationContext(
    scope_type="guild",
    scope_id="1",
    zip_code="06604",
    store_id="1956",
    store_name="Target Bridgeport",
    address_line="120 Hawley Ln",
    city="Trumbull",
    state="CT",
    postal_code="06611",
    latitude="41.230000",
    longitude="-73.150000",
)
PRODUCT = TargetCatalogProduct(
    product_key=f"target:{LOCATION.store_id}:{LOCATION.zip_code}:{TCIN}",
    tcin=TCIN,
    store_id=LOCATION.store_id,
    zip_code=LOCATION.zip_code,
    title="Example Console",
    product_url=f"https://www.target.com/p/example-console/-/A-{TCIN}",
    image_url="https://target.scene7.com/example.jpg",
)
OFFER = TargetOffer(
    tcin=TCIN,
    title="Example Console",
    product_url=PRODUCT.product_url,
    current_price=99.99,
    regular_price=399.99,
    image_url=PRODUCT.image_url,
    seller_name="Target",
    promotion_text="Target Circle deal",
    shipping_available=True,
    pickup_available=True,
    can_add_to_cart=True,
)
DECISION = TargetOfferDecision(
    product_key=PRODUCT.product_key,
    event_key="target-event:v1:test",
    event_type="regular_price_markdown",
    should_publish=True,
    current_price=99.99,
    reference_price=399.99,
    reference_source="target.redsky.product.price.reg_retail",
    discount_percent=75.0,
)
SETTINGS = TargetWatcherSettings(require_remote_database=False)


def verified_card():
    candidate = candidate_for_target_offer(
        PRODUCT,
        OFFER,
        DECISION,
        location=LOCATION,
        settings=SETTINGS,
    )
    return build_target_deal_card(candidate, event_key=DECISION.event_key)


def test_exact_target_card_passes_target_specific_public_gate() -> None:
    card = verified_card()
    assert is_verified_target_public_card(card, min_discount=50) is True
    assert card.public_post_key == DECISION.event_key
    assert card.selected_offer_id == f"target:{LOCATION.store_id}:{TCIN}"
    assert card.variant_attributes["targetIndependentConfirmation"] == "yes"
    assert card.variant_attributes["targetLocationScope"] == "local"
    assert card.variant_attributes["targetZip"] == LOCATION.zip_code


def test_target_public_gate_rejects_cross_domain_or_identity() -> None:
    wrong_domain = verified_card()
    wrong_domain.direct_product_url = "https://example.com/fake"
    wrong_domain.url = "https://example.com/fake"
    assert is_verified_target_public_card(wrong_domain, min_discount=10) is False

    mismatch = verified_card()
    mismatch.selected_offer_id = "target:9999:99999999"
    assert is_verified_target_public_card(mismatch, min_discount=10) is False


def test_target_public_gate_rejects_untrusted_or_unavailable_offer() -> None:
    untrusted = verified_card()
    untrusted.variant_attributes = deepcopy(untrusted.variant_attributes)
    untrusted.variant_attributes["trustedReferencePrice"] = "999.99"
    assert is_verified_target_public_card(untrusted, min_discount=10) is False

    unavailable = verified_card()
    unavailable.can_add_to_cart = False
    assert is_verified_target_public_card(unavailable, min_discount=10) is False


def test_target_public_gate_honors_server_threshold() -> None:
    card = verified_card()
    assert is_verified_target_public_card(card, min_discount=75) is True
    assert is_verified_target_public_card(card, min_discount=80) is False
