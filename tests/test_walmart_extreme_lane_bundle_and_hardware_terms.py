from __future__ import annotations

"""Bundle exception for the extreme-lane accessory test (audit finding 2)
and hardware-term recognition for VR/CPU/RAM/SSD/espresso/mixer (finding 6).

A listing that names a primary product and mentions an accessory only as an
included item ("TV with remote control", "laptop with charger included") is
a bundle, not an accessory. An accessory sold on its own ("Remote Control
for Samsung TV", "DeWalt 20V drill bit set") stays rejected.
"""

from dataclasses import replace

import pytest

from sniperplug.services import walmart_extreme_price_fanout as extreme
from sniperplug.services.walmart_extreme_price_signals import (
    exact_price_lead_evidence,
    is_accessory_title,
    listed_price_anomaly,
    primary_product_evidence,
)
from tests.test_walmart_extreme_price_alerts_295 import candidate


@pytest.fixture(autouse=True)
def _fanout_unit_price_recheck(monkeypatch):
    """Unit fanout uses static proven snapshots; live-API behavior has dedicated tests."""
    async def _verified(_candidate, *, page_price=False):
        return None
    monkeypatch.setattr(extreme, "live_extreme_offer_rejection_reason", _verified)


# Audit finding 2: the 8 realistic titles the old accessory test blocked.
# The drill bit set is a genuine accessory and stays rejected (see below).
BUNDLE_TITLES = (
    "Samsung 65 inch 4K Smart TV with remote control",
    "Nintendo Switch console bundle with carrying case",
    "Apple MacBook Air laptop with charger included",
    "Sony 75 inch TV with wall mount included",
    "Apple iPhone 13 with screen protector and case",
    "Dyson V15 vacuum with replacement battery",
    "Samsung Galaxy S24 phone with charger cable",
)

TRUE_ACCESSORIES = (
    "Remote Control for Samsung TV",
    "Carrying Case for Nintendo Switch",
    "DeWalt 20V Drill Bit Set",
    "iPhone Charger Cable",
    "Screen Protector for iPhone 13",
    "Replacement Battery for Dyson V15",
    "Samsung 65-inch TV Wall Mount",
    "TV Remote Control with Batteries Included",
)


@pytest.mark.parametrize("title", BUNDLE_TITLES)
def test_bundle_titles_are_not_accessories(title):
    assert not is_accessory_title(title)


@pytest.mark.parametrize("title", TRUE_ACCESSORIES)
def test_true_accessories_still_rejected(title):
    assert is_accessory_title(title)
    assert primary_product_evidence(candidate(title=title)) is None
    assert (
        extreme.extreme_rejection_reason(candidate(title=title))
        == "accessory_or_parts"
    )


@pytest.mark.parametrize(
    "title",
    (
        "Nintendo Switch console bundle with carrying case",
        "Apple MacBook Air laptop with charger included",
        "Apple iPhone 13 with screen protector and case",
        "Samsung Galaxy S24 phone with charger cable",
    ),
)
def test_bundle_titles_reach_the_extreme_lane(title):
    product = candidate(title=title, price=1.0)
    assert extreme.extreme_rejection_reason(product) is None
    card = extreme.build_extreme_price_card(product)
    assert card is not None and extreme.valid_extreme_price_card(card)


@pytest.mark.parametrize(
    "title",
    (
        "Samsung 65 inch 4K Smart TV with remote control",
        "Sony 75 inch TV with wall mount included",
    ),
)
def test_bundle_tv_titles_reach_the_extreme_lane_with_retailer_category(title):
    """Bare "TV" is not a hardware term; the retailer category carries it.

    A real Walmart TV listing has categoryPath Electronics/Televisions, so
    the bundle exception is what unblocks it — the category was already
    sufficient evidence once the accessory test stopped rejecting it.
    """
    base = candidate(title=title, price=1.0)
    product = replace(
        base,
        variant_attributes={
            **base.variant_attributes,
            "categoryPath": "Electronics/Televisions",
        },
    )
    assert extreme.extreme_rejection_reason(product) is None
    card = extreme.build_extreme_price_card(product)
    assert card is not None and extreme.valid_extreme_price_card(card)


def test_bundle_vacuum_title_passes_accessory_test_but_needs_hardware_term():
    """The Dyson bundle is no longer an accessory, but "vacuum" is not a
    hardware term in either extreme-lane list, so the public card still
    needs a follow-up term addition. Discovery priority already applies
    via the retailer category."""
    title = "Dyson V15 vacuum with replacement battery"
    assert not is_accessory_title(title)
    base = candidate(title=title, price=1.0)
    vacuum = replace(
        base,
        variant_attributes={
            **base.variant_attributes,
            "categoryPath": "Home/Appliances/Vacuums",
        },
    )
    assert primary_product_evidence(vacuum) == "walmart_category"
    assert extreme.extreme_rejection_reason(vacuum) == "insufficient_value_evidence"


# Audit finding 6: hardware classes the extreme lane did not recognize.
NEW_HARDWARE_TITLES = (
    "Meta Quest 3 VR Headset 128GB",
    "Meta Quest 3",
    "AMD Ryzen 7 7800X3D Processor",
    "Intel Core i9-14900K CPU",
    "Corsair Vengeance 32GB DDR5 RAM Memory Kit",
    "Samsung 990 Pro 2TB NVMe SSD",
    "WD Black 1TB Solid State Drive",
    "Breville Barista Express Espresso Machine",
    "KitchenAid Artisan Series 5 Stand Mixer",
)


@pytest.mark.parametrize("title", NEW_HARDWARE_TITLES)
def test_new_hardware_terms_reach_the_extreme_lane(title):
    product = candidate(title=title, price=1.0)
    assert exact_price_lead_evidence(product) == "high_value_hardware"
    assert extreme.extreme_rejection_reason(product) is None
    card = extreme.build_extreme_price_card(product)
    assert card is not None and extreme.valid_extreme_price_card(card)


def test_listed_price_anomaly_covers_new_hardware_terms():
    """A $49 SSD against a $599 Walmart list qualifies via the hardware path."""
    base = candidate(title="Samsung 990 Pro 2TB NVMe SSD", price=49.0)
    ssd = replace(
        base,
        variant_attributes={**base.variant_attributes, "msrp": "599.00"},
    )
    assert listed_price_anomaly(ssd) == 599.0
    assert extreme.extreme_rejection_reason(ssd) is None


def test_espresso_coffee_beans_are_not_hardware():
    """The espresso term is scoped to machines/makers, not coffee itself."""
    beans = candidate(title="Lavazza Espresso Coffee Beans 2 lb Bag", price=1.0)
    assert exact_price_lead_evidence(beans) is None
