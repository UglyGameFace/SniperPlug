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


def test_bundle_vacuum_title_reaches_the_extreme_lane():
    """Deal-discovery improvement 4: "vacuum" is now a hardware term, so
    the Dyson bundle from the audit reaches the public card instead of
    stopping at insufficient value evidence. The bundle exception is what
    keeps it out of the accessory test; the hardware term is what lets it
    through the value gate."""
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
    assert exact_price_lead_evidence(vacuum) == "high_value_hardware"
    assert extreme.extreme_rejection_reason(vacuum) is None
    card = extreme.build_extreme_price_card(vacuum)
    assert card is not None and extreme.valid_extreme_price_card(card)


# "vacuum" as a hardware term must not promote vacuum parts: bags, filters,
# hoses, belts, brush heads, attachments and nozzles are accessories.
VACUUM_PART_TITLES = (
    "Dyson V15 Vacuum Bags 3 Pack",
    "Shark Vacuum Hose Replacement",
    "Vacuum Cleaner Filter",
    "Dyson Vacuum Belt",
)


@pytest.mark.parametrize("title", VACUUM_PART_TITLES)
def test_vacuum_parts_still_rejected(title):
    assert is_accessory_title(title)
    assert primary_product_evidence(candidate(title=title)) is None
    assert (
        extreme.extreme_rejection_reason(candidate(title=title))
        == "accessory_or_parts"
    )


@pytest.mark.parametrize(
    "title",
    (
        "Dyson V15 Detect Absolute Cordless Vacuum",
        "Shark Navigator Lift-Away Vacuum Cleaner",
    ),
)
def test_plain_vacuum_titles_reach_the_extreme_lane(title):
    product = candidate(title=title, price=1.0)
    assert exact_price_lead_evidence(product) == "high_value_hardware"
    assert extreme.extreme_rejection_reason(product) is None
    card = extreme.build_extreme_price_card(product)
    assert card is not None and extreme.valid_extreme_price_card(card)


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


def test_listed_price_anomaly_console_bundle_with_headset_still_fires():
    """Round 4 bundle guard: a console sold *with* a headset is a console,
    not an audio accessory — the headset screen must not condemn it."""
    base = candidate(title="PlayStation 5 Console with Headset", price=49.0)
    bundle = replace(
        base,
        variant_attributes={**base.variant_attributes, "msrp": "599.00"},
    )
    assert listed_price_anomaly(bundle) == 599.0
    assert extreme.extreme_rejection_reason(bundle) is None


def test_listed_price_anomaly_vr_headset_is_primary_hardware():
    """Round 4 VR exception: a VR headset is a primary device, not an
    audio accessory — its listed-price anomaly still qualifies."""
    base = candidate(title="Meta Quest 3 VR Headset 128GB", price=49.0)
    quest = replace(
        base,
        variant_attributes={**base.variant_attributes, "msrp": "599.00"},
    )
    assert listed_price_anomaly(quest) == 599.0
    assert extreme.extreme_rejection_reason(quest) is None


def test_espresso_coffee_beans_are_not_hardware():
    """The espresso term is scoped to machines/makers, not coffee itself."""
    beans = candidate(title="Lavazza Espresso Coffee Beans 2 lb Bag", price=1.0)
    assert exact_price_lead_evidence(beans) is None
