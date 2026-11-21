"""Personal DM alerts for evidence-backed listing-error leads.

Before this fix, every listing-error lead fell into the no-reference
exact-price lead path in ``match_dm_deal``: the lead card deliberately
carries no verified was-price fields, so the matcher demanded an explicit
product/brand interest — and a subscriber whose picks lived only in route
+ category selections (or whose interest tokens were unavailable) never
got the DM, however strong the detector's value evidence was.

The classification fix: a listing lead whose source candidate carries a
trusted reference price that the detector itself used as value evidence
(reference/list/context, or the existing cross-seller marketplace comp)
is NOT a no-reference lead. It ranks as a referenced deal on the
price-mistake route — the implied gap versus the reference must clear the
subscriber's own discount floor, and the category / internal score /
price-cap / profit-floor guards apply unchanged. A listing lead WITHOUT
trusted reference evidence keeps the old interest-only rule, exactly.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_deal_routes import dm_route_tokens
from sniperplug.services.listing_contradiction import (
    build_listing_error_lead_card,
    listing_lead_reference_price,
)


def _candidate(**overrides) -> SourceCandidate:
    base = dict(
        source_key="walmart",
        retailer="Walmart",
        title="Little Hotties Foot Warmers 20-Pack",
        description="Each package contains 5 pairs of foot warmers.",
        product_url="https://www.walmart.com/ip/123456789",
        current_price=9.00,
        typical_price=66.00,
        api_reference_price=66.00,
        stock_status="In Stock",
        can_add_to_cart=True,
        seller_name="Walmart",
    )
    base.update(overrides)
    return SourceCandidate(**base)


def _pref(*, routes=("price_glitch", "hot_deals"), min_discount=50, **kw):
    return DmDealAlertPreference(
        user_id=7,
        enabled=True,
        min_discount=min_discount,
        categories=tuple(dm_route_tokens(routes)),
        **kw,
    )


def _strip_reference(candidate: SourceCandidate) -> SourceCandidate:
    """Same contradiction, no trusted reference anywhere on the candidate."""
    candidate.typical_price = None
    candidate.api_reference_price = None
    return candidate


# ------------------------------------------------------- evidence-backed


def test_evidence_backed_listing_lead_matches_on_route_and_floor() -> None:
    card = build_listing_error_lead_card(_candidate())
    assert card is not None
    decision = match_dm_deal(_pref(), card)
    assert decision.matched, decision.reason
    # The trace names the basis: implied gap vs the reference, the floor,
    # and (appended by the shared suffix logic) the price-mistake route.
    assert "Listing-error lead" in decision.reason
    assert "86% implied gap vs $66.00 reference" in decision.reason
    assert "not a verified markdown" in decision.reason
    assert "meets your 50% floor" in decision.reason
    assert "route" in decision.reason


def test_listing_lead_reference_price_uses_detector_evidence() -> None:
    assert listing_lead_reference_price(_candidate()) == 66.00
    assert listing_lead_reference_price(_strip_reference(_candidate())) is None


def test_evidence_backed_listing_lead_route_off_no_dm() -> None:
    card = build_listing_error_lead_card(_candidate())
    decision = match_dm_deal(_pref(routes=("hot_deals",)), card)
    assert not decision.matched


def test_evidence_backed_listing_lead_below_floor_no_dm() -> None:
    # Same evidence-backed lead, stricter subscriber floor: the implied
    # gap is 86%, so a 90% floor rejects it — the floor, not a gate,
    # decides, and the reason names the floor.
    card = build_listing_error_lead_card(_candidate())
    assert card is not None
    decision = match_dm_deal(_pref(min_discount=90), card)
    assert not decision.matched
    assert "below the required 90%" in decision.reason
    # Reference $16: the detector's smaller-share evidence holds
    # (20-pack vs 5 pairs at $9 ≈ the 10-item share of $16), so the lead
    # IS evidence-backed — but the implied gap is only 44%, under a 50%
    # floor, and it does not DM.
    candidate = _candidate(typical_price=16.00, api_reference_price=16.00)
    card16 = build_listing_error_lead_card(candidate)
    assert card16 is not None
    assert listing_lead_reference_price(candidate) == 16.00
    assert not match_dm_deal(_pref(min_discount=50), card16).matched


def test_evidence_backed_listing_lead_respects_max_price_cap() -> None:
    card = build_listing_error_lead_card(_candidate())
    decision = match_dm_deal(_pref(max_price_cents=500), card)
    assert not decision.matched
    assert "maximum" in decision.reason


def test_like_new_evidence_backed_lead_matches_condition_labelled() -> None:
    candidate = _candidate(condition="Like New")
    card = build_listing_error_lead_card(candidate)
    assert card is not None
    decision = match_dm_deal(_pref(), card)
    assert decision.matched, decision.reason
    assert "condition Like New — NOT a new item" in decision.reason
    # The DM card itself carries the same plain label.
    embed_text = " ".join(
        f"{field.name} {field.value}" for field in card.embed.fields
    )
    assert "NOT a new item" in embed_text


def test_condition_contradiction_only_lead_with_price_evidence_matches() -> None:
    # No quantity claims at all: title says Brand New, the offer says
    # Used - Like New, and a $66 reference stands against the $9 price.
    candidate = _candidate(
        title="Brand New Wireless Earbuds with Charging Case",
        description="Wireless earbuds with charging case and ear tips.",
        condition="Used - Like New",
    )
    card = build_listing_error_lead_card(candidate)
    assert card is not None
    decision = match_dm_deal(_pref(), card)
    assert decision.matched, decision.reason
    assert "condition Like New — NOT a new item" in decision.reason


def test_marketplace_comp_price_counts_as_trusted_reference() -> None:
    # No list/typical reference: the only evidence is the cross-seller
    # marketplace comp the Walmart machinery already carries.
    candidate = _candidate(
        typical_price=None,
        api_reference_price=None,
        variant_attributes={"marketplaceCompPrice": "66.00"},
    )
    card = build_listing_error_lead_card(candidate)
    assert card is not None
    assert listing_lead_reference_price(candidate) == 66.00
    assert match_dm_deal(_pref(), card).matched


# ------------------------------------------------------- no evidence: old rule


def test_no_evidence_listing_lead_still_needs_interest() -> None:
    card = build_listing_error_lead_card(_candidate())
    assert card is not None
    # Re-point the card at the same contradiction with NO trusted
    # reference: the detector would not call this a lead, so DM matching
    # must fall back to the exact-price lead rule.
    card.listing_contradiction_candidate = _strip_reference(_candidate())
    decision = match_dm_deal(_pref(), card)
    assert not decision.matched
    assert "explicit product or brand interest" in decision.reason


def test_no_evidence_listing_lead_still_matches_explicit_interest() -> None:
    card = build_listing_error_lead_card(_candidate())
    assert card is not None
    card.listing_contradiction_candidate = _strip_reference(_candidate())
    pref = DmDealAlertPreference(
        user_id=7,
        enabled=True,
        min_discount=50,
        categories=("brand:little hotties",),
    )
    decision = match_dm_deal(pref, card)
    assert decision.matched, decision.reason
    assert "Exact-price lead" in decision.reason


# ------------------------------------------------------- replay: ordinary deals


def _verified_card(*, discount=60.0, score=120):
    embed = discord.Embed(title="🔥 60% OFF • Apple AirPods Pro 2nd Gen")
    embed.add_field(name="Product Proof", value="Brand: Apple")
    return SimpleNamespace(
        label="Apple AirPods Pro 2nd Gen",
        url="https://www.walmart.com/ip/123",
        embed=embed,
        retailer="Walmart",
        api_current_price=99.0,
        current_price=99.0,
        api_reference_price=249.0,
        typical_price=249.0,
        api_discount_percent=discount,
        discount=discount,
        score=score,
    )


def _open_pref(*, min_discount=50):
    """A subscriber with no picks at all: eligible for everything, with
    only the hard guards (floor / score / caps) deciding."""
    return DmDealAlertPreference(
        user_id=7, enabled=True, min_discount=min_discount, categories=()
    )


def test_ordinary_verified_deal_still_matches() -> None:
    decision = match_dm_deal(_open_pref(), _verified_card())
    assert decision.matched, decision.reason


def test_ordinary_verified_deal_below_floor_still_rejected() -> None:
    decision = match_dm_deal(_open_pref(), _verified_card(discount=30.0))
    assert not decision.matched
