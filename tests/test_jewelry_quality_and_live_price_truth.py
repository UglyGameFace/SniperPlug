from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import discord
import pytest

from sniperplug.services import walmart_extreme_price_fanout as extreme
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.jewelry_quality import (
    card_jewelry_rejection_reason,
    jewelry_quality_rejection_reason,
)
from sniperplug.services.public_deal_quality import public_deal_rejection_reason
from sniperplug.services.walmart_extreme_price_signals import (
    exact_price_lead_evidence,
    is_priority_extreme_discovery,
)
from tests.test_walmart_extreme_price_alerts_295 import candidate


def _card(title: str, *, material: str = ""):
    return SimpleNamespace(
        label=title,
        retailer="Walmart",
        variant_attributes={"categoryPath": "Jewelry/Necklaces", "material": material},
        embed=discord.Embed(title=title),
        api_current_price=2.61,
        api_reference_price=8.92,
        api_discount_percent=71.0,
        score=183,
        current_price=2.61,
        typical_price=8.92,
        discount=71.0,
        url="https://www.walmart.com/ip/98765",
    )


@pytest.mark.parametrize("title", [
    "Time and Tru Women's Goldtone Initial U Necklace with Giftable Pouch",
    "14K Gold Plated Diamond Accent Ring",
    "Faux Diamond Pendant Necklace",
    "Gold Tone Costume Earrings",
    "Sterling Silver Plated Bracelet",
    "Cubic Zirconia Stud Earrings",
])
def test_fashion_and_plated_jewelry_cannot_be_precious_metal_alerts(title):
    card = _card(title)
    assert card_jewelry_rejection_reason(card) == "fashion_or_plated_jewelry"
    assert public_deal_rejection_reason(card, min_discount=1) == "fashion_or_plated_jewelry"
    pref = DmDealAlertPreference(
        user_id=1, enabled=True, mode="all", min_discount=20, min_score=0,
        categories=("dm_route:coupon_stack",),
    )
    assert match_dm_deal(pref, card).reason == "fashion_or_plated_jewelry"


@pytest.mark.parametrize("title", [
    "14K Solid Gold Rope Chain Necklace",
    "925 Sterling Silver Heart Pendant",
    "18K Gold Wedding Ring",
    "Platinum 950 Engagement Ring",
])
def test_precious_metal_claims_remain_eligible_for_deal_evaluation(title):
    assert jewelry_quality_rejection_reason(title) is None


def test_material_proof_uses_metal_fields_not_color_or_marketing():
    assert jewelry_quality_rejection_reason(
        "Vintage Pendant Necklace", attributes={"Metal Type": "925 Sterling Silver", "color": "gold"}
    ) is None
    assert jewelry_quality_rejection_reason(
        "Gold Necklace", attributes={"color": "gold", "brand": "Lux"}
    ) == "precious_jewelry_material_unproven"
    assert jewelry_quality_rejection_reason(
        "14K Gold Pendant Necklace", attributes={"material": "gold plated"}
    ) == "fashion_or_plated_jewelry"
    assert jewelry_quality_rejection_reason("Ring Video Doorbell 4") is None
    assert jewelry_quality_rejection_reason("USB Headset") is None


def test_unverified_msrp_cannot_create_extreme_headset_alert():
    headset = candidate(title="Logitech H390 Wired USB Headset, Black", price=29.56)
    headset.variant_attributes["msrp"] = "240.00"
    assert not is_priority_extreme_discovery(
        headset, current_price=29.56, trusted_reference=None
    )
    assert exact_price_lead_evidence(headset) is None
    assert extreme.build_extreme_price_card(headset) is None


@pytest.mark.parametrize("kind,expected", [
    ("same", None),
    ("price_changed", "live_price_changed"),
    ("offer_changed", "live_offer_or_variant_changed"),
    ("seller_changed", "live_offer_or_variant_changed"),
    ("out_of_stock", "live_not_buyable"),
    ("missing", "live_fetch_unverified"),
])
def test_live_exact_recheck_refuses_stale_price_or_different_variant(
    monkeypatch, kind, expected
):
    base = candidate(title="Samsung Galaxy Tab S11 Tablet", price=1.0)
    updated = replace(base, variant_attributes=dict(base.variant_attributes))
    if kind == "price_changed":
        updated.api_current_price = 24.88
        updated.current_price = 24.88
    elif kind == "offer_changed":
        updated.selected_offer_id = "different-offer"
    elif kind == "seller_changed":
        updated.seller_name = "Other Seller"
    elif kind == "out_of_stock":
        updated.can_add_to_cart = False

    monkeypatch.setattr(extreme.provider_registry, "get", lambda key: object())

    async def fake_fetch(provider, claim, *, timeout_seconds):
        assert claim.item_id == base.product_id
        assert claim.apparent_current_cents is None
        assert timeout_seconds <= 12.0
        if kind == "missing":
            return None, "retry", "Walmart temporarily unavailable"
        return updated, "verified", ""

    monkeypatch.setattr(extreme, "_fetch_exact_candidate_off_loop", fake_fetch)
    assert asyncio.run(extreme.live_extreme_offer_rejection_reason(base)) == expected


def test_live_check_fails_closed_without_registered_walmart_provider(monkeypatch):
    monkeypatch.setattr(extreme.provider_registry, "get", lambda key: None)
    assert asyncio.run(
        extreme.live_extreme_offer_rejection_reason(candidate())
    ) == "live_provider_unavailable"
