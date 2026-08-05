from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.cogs.dm_deal_alerts import build_dm_settings_embed
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_personal_categories import (
    category_key_for_card,
    split_exclude_terms,
    update_category_mutes,
)


def _card(title: str, *, attrs: dict | None = None):
    return SimpleNamespace(
        label=title,
        url="https://www.walmart.com/ip/123",
        embed=discord.Embed(title=f"🔥 60% OFF • {title}"),
        api_current_price=40.0,
        current_price=40.0,
        api_reference_price=100.0,
        typical_price=100.0,
        api_discount_percent=60.0,
        discount=60.0,
        score=120,
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
        exclude_keywords=("category:baby_kids",),
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


def test_mute_and_unmute_preserve_normal_excluded_words() -> None:
    muted = update_category_mutes(
        ("refurbished", "clearance"),
        add="baby",
    )
    keywords, categories = split_exclude_terms(muted)
    assert keywords == ("refurbished", "clearance")
    assert categories == ("baby_kids",)

    restored = update_category_mutes(muted, remove="baby")
    keywords, categories = split_exclude_terms(restored)
    assert keywords == ("refurbished", "clearance")
    assert categories == ()


def test_settings_show_human_category_label_not_storage_token() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        exclude_keywords=("category:baby_kids", "refurbished"),
    )
    embed = build_dm_settings_embed(preference)
    description = embed.description or ""

    assert "Muted DM categories: **Baby / Kids**" in description
    assert "Exclude words: **refurbished**" in description
    assert "category:baby_kids" not in description
