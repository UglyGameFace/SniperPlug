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
    update_favorite_categories,
    update_flip_settings,
    update_muted_categories,
)
from sniperplug.services.dm_product_interests import (
    match_product_preferences,
    parse_product_interest_input,
    update_product_preferences,
)
from sniperplug.services.opportunity_watchlist import category_for_title
from sniperplug.services.routing import PRICE_GLITCH_ROUTE, HOT_DEALS_ROUTE


def _card(*, glitch: bool = True, with_deal: bool = True):
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
        score=150,
        deal=deal if with_deal else None,
    )


def _pref(*tokens: str, max_price: int | None = None, min_discount: int = 20):
    return DmDealAlertPreference(
        user_id=42,
        enabled=True,
        mode="all",
        min_discount=min_discount,
        min_score=10,
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


def test_route_subscription_is_an_alternative_to_stale_product_and_category_filters():
    old = update_product_preferences(
        ("muted:desktop_computers", "gpus"),
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
    assert not match_dm_deal(
        DmDealAlertPreference(
            user_id=42, enabled=True, mode="all", min_discount=20,
            min_score=200, categories=selected,
        ),
        _card(),
    ).matched


def test_route_preferences_survive_other_personalization_edits():
    old = (*dm_route_tokens(("price_glitch",)), "favorite:ram")
    with_products = update_product_preferences(old, add_interests=("iphones",))
    with_flip = update_flip_settings(with_products, enabled=True)
    with_mute = update_muted_categories(with_flip, add=("baby",))
    with_favorite = update_favorite_categories(with_mute, add=("gaming",))
    assert split_dm_route_preferences(with_favorite) == ("price_glitch",)
    selected_categories, favorites = split_category_preferences(with_favorite)
    assert "dm_route:price_glitch" not in selected_categories
    assert "desktop_computers" in favorites


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
    assert "Price Glitches" in response.embed.description
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
