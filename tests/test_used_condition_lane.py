"""Used-condition lane (trust fix 1, 2026-11-18).

Plain "Used" matched neither the open-box nor the restored condition term
lists, so a used offer fell through to the ordinary verified-markdown lane
and posted as a normal "Verified Markdown" with its condition visible only
in small print — and Find/Hunt embeds had no condition field at all. These
tests pin the fix: used is its own labeled condition lane with the same
extra proof requirements as the other non-new lanes, a used offer can
never wear the plain verified-markdown label, and Find/Hunt embeds show a
condition field for anything that is not new. A new offer is unchanged.
"""

from __future__ import annotations

import discord

from sniperplug.cogs.deal_scanner import DealCard, build_deal_card_embed
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import _walmart_deal_lane
from sniperplug.services import public_deal_quality as quality
from sniperplug.services.candidate_pipeline import evaluate_candidate


def _card(
    *,
    condition: str | None = "Used",
    lane: str | None = None,
    current: float | None = 20.0,
    reference: float | None = 100.0,
) -> DealCard:
    card = DealCard(
        embed=discord.Embed(title="Widget"),
        url="https://www.walmart.com/ip/123456",
        label="Widget",
        score=120,
        discount=80.0,
        deal_lane=lane,
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=80.0 if reference else None,
        api_condition=condition,
        api_reference_path="priceInfo.wasPrice" if reference else None,
        direct_product_url="https://www.walmart.com/ip/123456",
        variant_attributes={},
    )
    card.current_price = current
    card.typical_price = reference
    return card


def test_used_condition_detection() -> None:
    assert quality.is_used_condition("Used")
    assert quality.is_used_condition("Used - Very Good")
    assert quality.is_used_condition("used acceptable")
    assert not quality.is_used_condition("New")
    assert not quality.is_used_condition("Unused")
    assert not quality.is_used_condition("Open Box")
    assert not quality.is_used_condition("Pre-Owned")
    assert not quality.is_used_condition("")


def test_provider_lane_routes_used_to_used_lane() -> None:
    kwargs = dict(proof_attrs={}, has_walmart_cash=False, has_cart_promo=False, has_onepay=False)
    assert _walmart_deal_lane(condition="Used - Acceptable", **kwargs) == quality.LANE_USED
    assert _walmart_deal_lane(condition="Used", **kwargs) == quality.LANE_USED
    assert _walmart_deal_lane(condition="New", **kwargs) == quality.LANE_VERIFIED_MARKDOWN
    assert _walmart_deal_lane(condition="Refurbished", **kwargs) == quality.LANE_RESTORED_REFURBISHED
    assert _walmart_deal_lane(condition="Open Box", **kwargs) == quality.LANE_OPEN_BOX_LIKE_NEW


def test_used_offer_posts_under_the_used_label() -> None:
    card = _card(condition="Used - Very Good")
    assert quality.public_deal_rejection_reason(card, min_discount=40) is None
    assert quality.prepare_public_deal_candidate(card, min_discount=40) is True
    assert card.deal_lane == quality.LANE_USED
    lane_fields = [
        field.value
        for field in card.embed.fields
        if str(field.name) == quality.PUBLIC_DEAL_LANE_FIELD
    ]
    assert lane_fields and "**Used**" in lane_fields[0]
    assert "Verified Markdown" not in lane_fields[0]


def test_used_offer_mislabeled_verified_markdown_fails_closed() -> None:
    card = _card(condition="Used", lane=quality.LANE_VERIFIED_MARKDOWN)
    assert (
        quality.public_deal_rejection_reason(card, min_discount=40)
        == "used_condition_not_labeled"
    )
    assert quality.is_public_deal_candidate(card, min_discount=40) is False


def test_used_lane_requires_structured_reference_proof() -> None:
    card = _card(condition="Used", reference=None)
    card.api_discount_percent = None
    assert (
        quality.public_deal_rejection_reason(card, min_discount=40)
        == "condition_reference_proof_missing"
    )


def test_used_lane_requires_a_condition() -> None:
    card = _card(condition=None, lane=quality.LANE_USED)
    assert (
        quality.public_deal_rejection_reason(card, min_discount=40)
        == "condition_missing"
    )


def test_used_lane_rejects_a_non_used_condition() -> None:
    card = _card(condition="New", lane=quality.LANE_USED)
    assert (
        quality.public_deal_rejection_reason(card, min_discount=40)
        == "used_condition_mismatch"
    )


def test_new_offer_is_unchanged() -> None:
    card = _card(condition="New")
    assert quality.public_deal_rejection_reason(card, min_discount=40) is None
    assert quality.prepare_public_deal_candidate(card, min_discount=40) is True
    assert card.deal_lane == quality.LANE_VERIFIED_MARKDOWN
    lane_fields = [
        field.value
        for field in card.embed.fields
        if str(field.name) == quality.PUBLIC_DEAL_LANE_FIELD
    ]
    assert lane_fields and "**Verified Markdown**" in lane_fields[0]


def _embed_for_condition(condition: str | None) -> discord.Embed:
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Widget 2000",
        product_url="https://www.walmart.com/ip/123456",
        current_price=20.0,
        typical_price=100.0,
        condition=condition,
        product_id="123456",
        selected_offer_id="123456",
        seller_name="Walmart",
    )
    decision = evaluate_candidate(candidate)
    return build_deal_card_embed(candidate, decision.deal, decision, 80.0)


def test_find_embed_shows_condition_for_used_offer() -> None:
    embed = _embed_for_condition("Used")
    fields = {str(field.name): str(field.value) for field in embed.fields}
    assert "🏷️ Condition" in fields
    assert "Used" in fields["🏷️ Condition"]


def test_find_embed_shows_condition_for_refurbished_offer() -> None:
    embed = _embed_for_condition("Refurbished")
    fields = {str(field.name): str(field.value) for field in embed.fields}
    assert "🏷️ Condition" in fields
    assert "Refurbished" in fields["🏷️ Condition"]


def test_find_embed_hides_condition_field_for_new_offer() -> None:
    for condition in ("New", "new", None, ""):
        embed = _embed_for_condition(condition)
        names = [str(field.name) for field in embed.fields]
        assert "🏷️ Condition" not in names
