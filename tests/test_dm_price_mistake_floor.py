from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.cogs.dm_deal_alerts import build_dm_settings_embed
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_deal_routes import dm_route_tokens
from sniperplug.services.dm_personal_categories import (
    explicit_flip_min_profit_cents,
    flip_settings,
    split_category_preferences,
    update_flip_settings,
)


def _glitch_card(
    title: str,
    *,
    current: float,
    reference: float,
    discount: float,
    score: int = 150,
):
    deal = NormalizedDeal(
        title=title,
        retailer="Walmart",
        product_url="https://www.walmart.com/ip/123",
        current_price=current,
        typical_price=reference,
        discount_percent=discount,
        is_possible_price_error=True,
    )
    return SimpleNamespace(
        label=title,
        retailer="Walmart",
        url=deal.product_url,
        embed=discord.Embed(title=title),
        variant_attributes={},
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=discount,
        score=score,
        deal=deal,
    )


def _pref(*tokens: str, min_discount: int = 20) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=min_discount,
        categories=tuple(tokens),
    )


def test_price_mistake_route_alone_must_clear_the_profit_floor() -> None:
    pref = _pref(*dm_route_tokens(("price_glitch",)), "flip_profit:3000")

    small_spread = match_dm_deal(
        pref,
        _glitch_card(
            "Store Brand Toaster",
            current=80.0,
            reference=100.0,
            discount=20.0,
        ),
    )
    big_spread = match_dm_deal(
        pref,
        _glitch_card(
            "HP OMEN gaming desktop PC",
            current=99.0,
            reference=999.0,
            discount=90.0,
        ),
    )

    assert small_spread.matched is False
    assert "below your $30.00 minimum" in small_spread.reason
    assert big_spread.matched is True


def test_profit_floor_does_not_block_a_deal_matching_another_layer() -> None:
    # The same small-spread price mistake also sits in a picked category,
    # so the floor (which only gates route-only matches) does not apply.
    pref = _pref(
        *dm_route_tokens(("price_glitch",)),
        "flip_profit:3000",
        "apple",
    )

    decision = match_dm_deal(
        pref,
        _glitch_card(
            "Apple AirPods Pro 2nd Gen",
            current=80.0,
            reference=100.0,
            discount=20.0,
        ),
    )

    assert decision.matched is True


def test_no_stored_floor_means_no_gate() -> None:
    pref = _pref(*dm_route_tokens(("price_glitch",)))

    decision = match_dm_deal(
        pref,
        _glitch_card(
            "Store Brand Toaster",
            current=80.0,
            reference=100.0,
            discount=20.0,
        ),
    )

    assert decision.matched is True
    assert explicit_flip_min_profit_cents(pref.categories) == 0


def test_flip_floor_tokens_round_trip_and_preserve_other_picks() -> None:
    categories = update_flip_settings(
        ("apple", *dm_route_tokens(("price_glitch",))),
        enabled=True,
        minimum_profit_cents=7_500,
    )
    enabled, minimum_profit = flip_settings(categories)

    assert split_category_preferences(categories) == ("apple",)
    assert enabled is True
    assert minimum_profit == 7_500
    assert explicit_flip_min_profit_cents(categories) == 7_500

    disabled = update_flip_settings(categories, enabled=False)
    enabled, minimum_profit = flip_settings(disabled)
    assert enabled is False
    assert minimum_profit == 7_500
    assert split_category_preferences(disabled) == ("apple",)


def test_settings_embed_uses_the_sentence_without_internal_tokens() -> None:
    preference = _pref(
        *dm_route_tokens(("price_glitch",)),
        "flip_profit:7500",
        min_discount=50,
    )
    embed = build_dm_settings_embed(preference)
    description = embed.description or ""

    assert description.startswith("You'll get DMs for price mistakes")
    assert "price mistakes worth $75+" in description
    assert "flip_profit:7500" not in description
    assert "flip:enabled" not in description
