from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.cogs.dm_deal_alerts import build_dm_settings_embed
from sniperplug.cogs.dm_deal_preferences_view import DmDealPreferencesView
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_personal_categories import (
    all_personal_categories,
    category_key_for_card,
    compose_category_preferences,
    muted_category_preferences,
    personal_category_pages,
    search_personal_categories,
    split_category_preferences,
    split_exclude_terms,
    update_category_mutes,
    update_favorite_categories,
    update_muted_categories,
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


def test_personal_baby_mute_blocks_only_that_preference() -> None:
    card = _card("Gerber Baby Boys Pajama Set")
    muted = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="all",
        min_discount=30,
        min_score=50,
        categories=("muted:baby_kids",),
    )
    unmuted = DmDealAlertPreference(
        user_id=2,
        enabled=True,
        mode="all",
        min_discount=30,
        min_score=50,
    )

    muted_decision = match_dm_deal(muted, card)
    unmuted_decision = match_dm_deal(unmuted, card)

    assert muted_decision.matched is False
    assert muted_decision.reason == "category is muted in your personal DMs"
    assert muted_decision.category_key == "baby_kids"
    assert unmuted_decision.matched is True


def test_legacy_mute_tokens_remain_readable() -> None:
    muted = update_category_mutes(
        ("refurbished", "clearance"),
        add="baby",
    )
    keywords, categories = split_exclude_terms(muted)
    assert keywords == ("refurbished", "clearance")
    assert categories == ("baby_kids",)


def test_new_category_mutes_preserve_favorites_allowlist_and_flip_tokens() -> None:
    original = compose_category_preferences(
        ("apple",),
        ("gpus",),
        muted=("baby_kids",),
        flip_enabled=True,
        flip_min_profit_cents=7_500,
    )
    updated = update_muted_categories(
        original,
        add="pets, toys",
        remove="baby",
    )
    selected, favorites = split_category_preferences(updated)
    muted = muted_category_preferences(updated)

    assert selected == ("apple",)
    assert favorites == ("gpus",)
    assert muted == ("pet_supplies", "toys_collectibles")
    assert "flip:enabled" in updated
    assert "flip_profit:7500" in updated


def test_complete_catalog_is_paginated_not_truncated() -> None:
    catalog = all_personal_categories()
    pages = personal_category_pages()
    flattened = tuple(category for page in pages for category in page)

    assert catalog == OPPORTUNITY_CATEGORIES
    assert flattened == OPPORTUNITY_CATEGORIES
    assert len(catalog) > 25
    assert len(pages) == (len(catalog) + 24) // 25
    assert all(1 <= len(page) <= 25 for page in pages)


def test_menu_persists_every_category_without_keyword_cap() -> None:
    all_keys = tuple(category.key for category in OPPORTUNITY_CATEGORIES)
    stored = compose_category_preferences(
        (),
        (),
        muted=all_keys,
    )
    normalized = DmDealAlertPreference(
        user_id=1,
        categories=stored,
    ).normalized()

    assert muted_category_preferences(normalized.categories) == all_keys
    assert len(all_keys) > 12


def test_menu_builds_pages_from_the_live_catalog() -> None:
    view = DmDealPreferencesView(
        bot=SimpleNamespace(db=None),
        user_id=1,
        preference=DmDealAlertPreference(user_id=1),
    )

    assert sum(len(page) for page in view._pages()) == len(OPPORTUNITY_CATEGORIES)
    category_select = view.children[1]
    assert isinstance(category_select, discord.ui.Select)
    assert len(category_select.options) <= 25


def test_category_search_covers_labels_terms_and_drivers_without_mutation() -> None:
    full_before = all_personal_categories()
    tech = search_personal_categories("graphics")
    resale = search_personal_categories("resale value")
    baby = search_personal_categories("baby")

    assert any(category.key == "gpus" for category in tech)
    assert any(category.key == "sneakers" for category in resale)
    assert any(category.key == "baby_kids" for category in baby)
    assert all_personal_categories() == full_before


def test_favorite_categories_do_not_become_a_hard_allowlist() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="all",
        min_discount=30,
        min_score=50,
        categories=("favorite:gpus",),
    )

    decision = match_dm_deal(
        preference,
        _card("Apple AirPods Pro 2nd Gen"),
    )

    assert decision.matched is True
    assert decision.category_key == "apple"


def test_favorite_tech_gets_small_smart_priority_without_weakening_proof() -> None:
    card = _card(
        "NVIDIA RTX 5070 GPU",
        current=100.0,
        reference=145.0,
        discount=31.0,
        score=120,
    )
    normal = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=20,
        min_score=70,
    )
    favorite = DmDealAlertPreference(
        user_id=2,
        enabled=True,
        mode="smart",
        min_discount=20,
        min_score=70,
        categories=("favorite:gpus",),
    )

    normal_decision = match_dm_deal(normal, card)
    favorite_decision = match_dm_deal(favorite, card)

    assert normal_decision.matched is False
    assert normal_decision.required_discount == 35
    assert favorite_decision.matched is True
    assert favorite_decision.required_discount == 30
    assert "favorite-category priority" in favorite_decision.reason


def test_favorite_never_lowers_explicit_user_floor() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=40,
        min_score=70,
        categories=("favorite:gpus",),
    )
    decision = match_dm_deal(
        preference,
        _card(
            "NVIDIA RTX 5070 GPU",
            current=100.0,
            reference=145.0,
            discount=31.0,
        ),
    )

    assert decision.matched is False
    assert decision.required_discount == 40


def test_favorite_updates_preserve_optional_hard_allowlist() -> None:
    updated = update_favorite_categories(
        ("apple",),
        add="pc, gaming",
    )
    selected, favorites = split_category_preferences(updated)

    assert selected == ("apple",)
    assert "gpus" in favorites
    assert "cpus" in favorites
    assert "brand_direct_electronics" in favorites

    restored = update_favorite_categories(updated, remove="pc")
    selected, favorites = split_category_preferences(restored)
    assert selected == ("apple",)
    assert "gpus" not in favorites
    assert "brand_direct_electronics" in favorites


def test_settings_show_human_labels_not_storage_tokens() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        categories=(
            "favorite:gpus",
            "favorite:smart_home",
            "muted:baby_kids",
        ),
        exclude_keywords=("refurbished",),
    )
    embed = build_dm_settings_embed(preference)
    description = embed.description or ""

    assert "Allowed categories: **all categories**" in description
    assert "Favorite DM categories: **Graphics Cards, Smart Home / Security**" in description
    assert "Muted DM categories: **Baby / Kids**" in description
    assert "Exclude words: **refurbished**" in description
    assert "muted:baby_kids" not in description
    assert "favorite:gpus" not in description
