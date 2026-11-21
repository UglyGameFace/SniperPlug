from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.cogs.dm_deal_alerts import build_dm_settings_embed
from sniperplug.cogs.dm_deal_preferences_view import (
    SCREEN_CATEGORIES,
    DmDealPreferencesView,
    _CategoryPageSelect,
)
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_personal_categories import (
    all_personal_categories,
    category_key_for_card,
    compose_category_preferences,
    personal_category_pages,
    search_personal_categories,
    split_category_preferences,
)
from sniperplug.services.opportunity_watchlist import OPPORTUNITY_CATEGORIES


def _card(
    title: str,
    *,
    current: float = 40.0,
    reference: float = 100.0,
    discount: float = 60.0,
    score: int = 120,
    attrs: dict | None = None,
):
    return SimpleNamespace(
        label=title,
        url="https://www.walmart.com/ip/123",
        embed=discord.Embed(title=f"🔥 {discount:.0f}% OFF • {title}"),
        api_current_price=current,
        current_price=current,
        api_reference_price=reference,
        typical_price=reference,
        api_discount_percent=discount,
        discount=discount,
        score=score,
        variant_attributes=dict(attrs or {}),
    )


def test_baby_clothing_is_classified_as_baby_kids() -> None:
    assert category_key_for_card(_card("Gerber Baby Girls 3-Piece Bodysuit Set")) == "baby_kids"
    assert category_key_for_card(_card("Newborn Sleep and Play Outfit")) == "baby_kids"
    assert category_key_for_card(
        _card("Organic Cotton Outfit", attrs={"department": "Baby Clothing"})
    ) == "baby_kids"


def test_baby_word_does_not_blindly_hide_unrelated_collectibles() -> None:
    card = _card("Star Wars Baby Yoda Collectible Toy")
    assert category_key_for_card(card) == "toys_collectibles"


def test_muted_tokens_no_longer_block_anything() -> None:
    card = _card("Gerber Baby Boys Pajama Set")
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("muted:baby_kids",),
    )

    decision = match_dm_deal(preference, card)

    assert decision.matched is True
    assert decision.category_key == "baby_kids"
    assert split_category_preferences(preference.normalized().categories) == ()


def test_favorite_tokens_never_subscribe_the_category() -> None:
    boots = _card(
        "Madden NYC Men's Noryx Chelsea Boots",
        current=22.49,
        reference=65.0,
        discount=65.0,
        score=232,
    )
    assert category_key_for_card(boots) == "shoes_apparel"

    # Felix's shape: shoes_apparel exists only as a legacy favorite, next
    # to a genuine pick. The favorite must not let the boots in.
    favorite_only_pick = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("favorite:shoes_apparel", "gpus"),
    )
    assert split_category_preferences(
        favorite_only_pick.normalized().categories
    ) == ("gpus",)
    assert match_dm_deal(favorite_only_pick, boots).matched is False

    # The same boots deal matches once shoes_apparel is a plain selected
    # category.
    subscribed = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("shoes_apparel", "gpus"),
    )
    assert match_dm_deal(subscribed, boots).matched is True


def test_favorite_only_preference_drops_to_no_picks() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("favorite:gpus",),
    )

    assert preference.normalized().categories == ()
    assert split_category_preferences(preference.normalized().categories) == ()


def test_favorites_no_longer_change_the_discount_floor() -> None:
    card = _card(
        "NVIDIA RTX 5070 GPU",
        current=100.0,
        reference=145.0,
        discount=31.0,
        score=120,
    )
    plain = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=20,
    )
    favorite = DmDealAlertPreference(
        user_id=2,
        enabled=True,
        min_discount=20,
        categories=("favorite:gpus",),
    )

    plain_decision = match_dm_deal(plain, card)
    favorite_decision = match_dm_deal(favorite, card)

    assert plain_decision.matched is True
    assert favorite_decision.matched is True
    assert plain_decision.required_discount == 20
    assert favorite_decision.required_discount == 20
    assert "favorite" not in favorite_decision.reason


def test_compose_and_split_round_trip_selected_categories() -> None:
    stored = compose_category_preferences(
        ("apple",),
        flip_enabled=True,
        flip_min_profit_cents=7_500,
    )

    assert split_category_preferences(stored) == ("apple",)
    assert "flip:enabled" in stored
    assert "flip_profit:7500" in stored


def test_complete_catalog_is_paginated_not_truncated() -> None:
    catalog = all_personal_categories()
    pages = personal_category_pages()
    flattened = tuple(category for page in pages for category in page)

    assert catalog == OPPORTUNITY_CATEGORIES
    assert flattened == OPPORTUNITY_CATEGORIES
    assert len(catalog) > 25
    assert len(pages) == (len(catalog) + 24) // 25
    assert all(1 <= len(page) <= 25 for page in pages)


def test_menu_builds_category_screen_from_the_live_catalog() -> None:
    view = DmDealPreferencesView(
        bot=SimpleNamespace(db=None),
        user_id=1,
        preference=DmDealAlertPreference(user_id=1),
    )

    assert sum(len(page) for page in view._pages()) == len(OPPORTUNITY_CATEGORIES)
    view.screen = SCREEN_CATEGORIES
    view._rebuild_items()
    selects = [
        item for item in view.children if isinstance(item, _CategoryPageSelect)
    ]
    assert len(selects) == 1
    assert len(selects[0].options) <= 25


def test_category_search_covers_labels_terms_and_drivers_without_mutation() -> None:
    full_before = all_personal_categories()
    tech = search_personal_categories("graphics")
    resale = search_personal_categories("resale value")
    baby = search_personal_categories("baby")

    assert any(category.key == "gpus" for category in tech)
    assert any(category.key == "sneakers" for category in resale)
    assert any(category.key == "baby_kids" for category in baby)
    assert all_personal_categories() == full_before


def test_settings_embed_shows_human_labels_not_storage_tokens() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        categories=(
            "favorite:gpus",
            "favorite:smart_home",
            "muted:baby_kids",
        ),
    )
    embed = build_dm_settings_embed(preference)
    description = embed.description or ""
    field_text = "\n".join(field.value for field in embed.fields)

    assert "muted:baby_kids" not in description
    assert "favorite:gpus" not in description
    assert "muted:baby_kids" not in field_text
    assert "favorite:gpus" not in field_text
    # Favorites are not subscriptions, so their labels must not show up
    # as category picks either.
    assert "Graphics Cards" not in field_text
    assert "Smart Home / Security" not in field_text
