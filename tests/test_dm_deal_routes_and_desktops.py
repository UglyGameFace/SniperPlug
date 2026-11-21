from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from sniperplug.cogs.dm_deal_preferences_view import DmDealPreferencesView
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_deal_routes import (
    PERSONAL_DEAL_ROUTES,
    dm_route_tokens,
    normalize_dm_routes,
    split_dm_route_preferences,
    verified_card_route,
)
from sniperplug.services.dm_personal_categories import (
    split_category_preferences,
    update_flip_settings,
)
from sniperplug.services.dm_product_interests import (
    match_product_preferences,
    parse_product_interest_input,
    update_product_preferences,
)
from sniperplug.services.opportunity_watchlist import category_for_title
from sniperplug.services.routing import PRICE_GLITCH_ROUTE, HOT_DEALS_ROUTE


def _card(*, glitch: bool = True, with_deal: bool = True, score: int = 150):
    title = "HP OMEN gaming desktop PC"
    deal = NormalizedDeal(
        title=title,
        retailer="Walmart",
        product_url="https://www.walmart.com/ip/123",
        current_price=80.0,
        typical_price=400.0,
        discount_percent=80.0,
        is_possible_price_error=glitch,
    )
    return SimpleNamespace(
        label=title,
        retailer="Walmart",
        url=deal.product_url,
        embed=discord.Embed(title=title),
        variant_attributes={"category": "Computers / Desktops"},
        api_current_price=80.0,
        api_reference_price=400.0,
        api_discount_percent=80.0,
        score=score,
        deal=deal if with_deal else None,
    )


def _pref(*tokens: str, max_price: int | None = None, min_discount: int = 20):
    return DmDealAlertPreference(
        user_id=42,
        enabled=True,
        min_discount=min_discount,
        max_price_cents=max_price,
        categories=tuple(tokens),
    )


def test_route_labels_normalize_and_unknown_routes_are_not_subscriptions():
    assert normalize_dm_routes("Price Glitches, hot deals, garbage") == (
        PRICE_GLITCH_ROUTE,
        HOT_DEALS_ROUTE,
    )
    assert split_dm_route_preferences(
        (*dm_route_tokens(("price_glitch", "hot_deals")), "favorite:ram")
    ) == (PRICE_GLITCH_ROUTE, HOT_DEALS_ROUTE)
    assert "staff_review" not in PERSONAL_DEAL_ROUTES
    assert "dead_reports" not in PERSONAL_DEAL_ROUTES
    assert "amazon" not in PERSONAL_DEAL_ROUTES


def test_route_match_works_alongside_stale_product_and_category_picks():
    old = update_product_preferences(
        ("gpus",),
        add_interests=("iphones",),
        add_brands=("Anker",),
    )
    pref = _pref(*old, *dm_route_tokens(("price_glitch",)))
    decision = match_dm_deal(pref, _card())
    assert decision.matched
    assert "route Price Glitches" in decision.reason
    assert "desktop_computers" in decision.reason


def test_nonselected_route_and_missing_deal_metadata_fail_closed():
    pref = _pref(*dm_route_tokens(("price_glitch",)))
    assert not match_dm_deal(pref, _card(glitch=False)).matched
    assert not match_dm_deal(pref, _card(with_deal=False)).matched
    assert verified_card_route(_card(with_deal=False)) is None


def test_route_subscriptions_never_bypass_explicit_hard_thresholds():
    selected = dm_route_tokens(("price_glitch",))
    assert not match_dm_deal(_pref(*selected, max_price=5_000), _card()).matched
    assert not match_dm_deal(_pref(*selected, min_discount=90), _card()).matched
    assert not match_dm_deal(_pref(*selected), _card(score=50)).matched


def test_union_interest_match_dms_even_when_the_route_is_not_selected():
    # The headline change: routes used to override every other pick. A
    # Walmart deal that matches a picked product now DMs even when its
    # route is not one of the picked deal types.
    pref = _pref(
        *dm_route_tokens(("price_glitch",)),
        "interest:desktop_computers",
    )
    decision = match_dm_deal(pref, _card(glitch=False))
    assert decision.matched
    assert "explicit product intent" in decision.reason


def test_union_hp_deal_matches_through_its_route():
    title = "HP OMEN gaming laptop"
    deal = NormalizedDeal(
        title=title,
        retailer="HP",
        product_url="https://www.hp.com/us-en/shop/pdp/omen",
        current_price=99.0,
        typical_price=999.0,
        discount_percent=90.0,
        is_possible_price_error=True,
    )
    card = SimpleNamespace(
        label=title,
        retailer="HP",
        url=deal.product_url,
        embed=discord.Embed(title=title),
        variant_attributes={},
        api_current_price=99.0,
        api_reference_price=999.0,
        api_discount_percent=90.0,
        score=150,
        deal=deal,
    )
    pref = _pref(*dm_route_tokens(("price_glitch",)), min_discount=50)
    decision = match_dm_deal(pref, card)
    assert decision.matched
    assert "route Price Glitches" in decision.reason


def test_union_product_picks_alone_do_not_gate_other_retailers():
    title = "HP OMEN gaming laptop"
    deal = NormalizedDeal(
        title=title,
        retailer="HP",
        product_url="https://www.hp.com/us-en/shop/pdp/omen",
        current_price=99.0,
        typical_price=999.0,
        discount_percent=90.0,
        is_possible_price_error=True,
    )
    card = SimpleNamespace(
        label=title,
        retailer="HP",
        url=deal.product_url,
        embed=discord.Embed(title=title),
        variant_attributes={},
        api_current_price=99.0,
        api_reference_price=999.0,
        api_discount_percent=90.0,
        score=150,
        deal=deal,
    )
    pref = _pref("interest:laptops", min_discount=50)
    # Product picks are a Walmart-only layer: silent here, not a veto, so
    # with no other applicable pick the HP deal stays eligible.
    assert match_dm_deal(pref, card).matched


def test_route_preferences_survive_other_personalization_edits():
    old = (*dm_route_tokens(("price_glitch",)), "favorite:ram")
    with_products = update_product_preferences(old, add_interests=("iphones",))
    with_flip = update_flip_settings(with_products, enabled=True)
    assert split_dm_route_preferences(with_flip) == ("price_glitch",)
    selected_categories = split_category_preferences(with_flip)
    assert "dm_route:price_glitch" not in selected_categories
    # The legacy favorite token is not a subscription, so it never lands
    # in the selected categories.
    assert "ram" not in selected_categories


def test_route_selection_round_trips_through_discord_menu_save(monkeypatch):
    import sniperplug.cogs.dm_deal_preferences_view as menu_module

    async def fake_save(_db, preference):
        return preference.normalized()

    class FakeResponse:
        async def edit_message(self, *, embed, view):
            self.embed = embed
            self.view = view

    monkeypatch.setattr(menu_module, "save_dm_deal_alert_preference", fake_save)
    view = DmDealPreferencesView(
        bot=SimpleNamespace(db=None), user_id=42, preference=_pref(),
    )
    view.selected_routes = {"price_glitch", "hot_deals"}
    view.dirty = True
    response = FakeResponse()
    asyncio.run(view._save(SimpleNamespace(response=response)))
    assert split_dm_route_preferences(view.preference.categories) == (
        "price_glitch", "hot_deals",
    )
    assert view.dirty is False
    # The home screen leads with the one-sentence summary in plain names.
    embed_text = (response.embed.description or "") + "\n" + "\n".join(
        f"{field.name}\n{field.value}" for field in response.embed.fields
    )
    assert "price mistakes" in embed_text
    assert "big markdowns" in embed_text
    assert response.view is view


def test_complete_desktop_computers_are_separate_from_parts_and_laptops():
    keys, unknown = parse_product_interest_input("desktop computers, laptops")
    assert unknown == ()
    assert keys == ("desktop_computers", "laptops")
    assert match_product_preferences(
        "HP OMEN Gaming Desktop PC RTX 5080",
        interests=("desktop_computers",), brands=(),
    ).matched
    assert match_product_preferences(
        "Apple Mac Mini M4 Desktop Computer",
        interests=("desktop_computers",), brands=(),
    ).matched
    for accessory in ("Gaming PC Case", "Gaming PC Keyboard Kit", "Gaming Laptop"):
        assert not match_product_preferences(
            accessory,
            interests=("desktop_computers",), brands=(),
        ).matched
    category = category_for_title("HP OMEN Gaming Desktop PC RTX 5080")
    assert category is not None and category.key == "desktop_computers"
