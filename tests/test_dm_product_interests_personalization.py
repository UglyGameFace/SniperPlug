from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_badges import personalize_dm_card_title
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_personal_categories import (
    split_category_preferences,
    update_favorite_categories,
    update_flip_settings,
    update_muted_categories,
)
from sniperplug.services.dm_product_interests import (
    match_product_preferences,
    parse_product_interest_input,
    split_product_preferences,
    update_product_preferences,
)


def _card(
    title: str,
    *,
    brand: str = "",
    category: str = "",
    current: float = 20.0,
    reference: float = 50.0,
    discount: float = 60.0,
    score: int = 120,
):
    attrs = {
        key: value
        for key, value in {
            "brand": brand,
            "category": category,
        }.items()
        if value
    }
    return SimpleNamespace(
        label=title,
        url="https://www.walmart.com/ip/123",
        embed=discord.Embed(title=f"💎 {discount:.0f}% OFF • {title}"),
        variant_attributes=attrs,
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=discount,
        current_price=current,
        typical_price=reference,
        discount=discount,
        score=score,
    )


def _preference(*, categories: tuple[str, ...]) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="all",
        min_discount=20,
        min_score=0,
        min_savings_cents=0,
        categories=categories,
    )


def test_interest_aliases_and_tokens_round_trip_without_becoming_categories() -> None:
    interests, unknown = parse_product_interest_input(
        "portable chargers, Pokemon TCG, designer cologne, NeeDoh"
    )
    assert unknown == ()
    assert interests == (
        "portable_chargers",
        "pokemon_tcg_sealed",
        "designer_fragrance",
        "needoh",
    )

    stored = update_product_preferences(
        ("favorite:mobile_accessories", "muted:baby_kids"),
        replace_interests=interests,
        replace_brands=("Anker", "Versace", "YSL"),
    )
    saved_interests, saved_brands = split_product_preferences(stored)
    selected, favorites = split_category_preferences(stored)

    assert saved_interests == interests
    assert saved_brands == ("anker", "versace", "ysl")
    assert selected == ()
    assert favorites == ("mobile_accessories",)


def test_existing_category_editors_preserve_product_and_brand_tokens() -> None:
    stored = update_product_preferences(
        ("toys_collectibles",),
        replace_interests=("pokemon_tcg_sealed",),
        replace_brands=("Pokemon",),
    )
    stored = update_favorite_categories(stored, add="toys")
    stored = update_muted_categories(stored, add="baby")
    stored = update_flip_settings(stored, enabled=True, minimum_profit_cents=5000)

    interests, brands = split_product_preferences(stored)
    assert interests == ("pokemon_tcg_sealed",)
    assert brands == ("pokemon",)
    assert "favorite:toys_collectibles" in stored
    assert "muted:baby_kids" in stored
    assert "flip:enabled" in stored


def test_portable_charger_and_brand_groups_are_both_required() -> None:
    categories = update_product_preferences(
        (),
        replace_interests=("portable_chargers",),
        replace_brands=("Anker",),
    )
    pref = _preference(categories=categories)

    matching = _card(
        "Anker PowerCore 20,000mAh USB-C Power Bank",
        brand="Anker",
        category="Portable Chargers",
    )
    other_brand = _card(
        "Belkin 20,000mAh USB-C Power Bank",
        brand="Belkin",
        category="Portable Chargers",
    )
    wrong_product = _card(
        "Anker 65W USB-C Wall Charger",
        brand="Anker",
        category="Wall Chargers",
    )

    decision = match_dm_deal(pref, matching)
    assert decision.matched is True
    assert "interest Portable chargers / power banks" in decision.reason
    assert "brand Anker" in decision.reason
    assert match_dm_deal(pref, other_brand).reason == "none of your selected brands matched"
    assert match_dm_deal(pref, wrong_product).reason == "none of your specific product interests matched"


def test_designer_fragrance_profile_rejects_body_spray_lookalikes() -> None:
    categories = update_product_preferences(
        (),
        replace_interests=("designer_fragrance",),
        replace_brands=("Versace", "Dior", "YSL"),
    )
    pref = _preference(categories=categories)

    cologne = _card(
        "Versace Eros Eau de Toilette EDT 3.4 oz",
        brand="Versace",
        category="Fragrance",
    )
    body_spray = _card(
        "Versace Eros Fragrance Body Spray 6 oz",
        brand="Versace",
        category="Fragrance",
    )

    assert match_dm_deal(pref, cologne).matched is True
    rejected = match_dm_deal(pref, body_spray)
    assert rejected.matched is False
    assert rejected.reason == "none of your specific product interests matched"


def test_pokemon_profile_is_sealed_tcg_not_generic_pokemon_merchandise() -> None:
    result = match_product_preferences(
        "Pokemon Scarlet & Violet Elite Trainer Box ETB Trading Card Game",
        interests=("pokemon_tcg_sealed",),
    )
    assert result.matched is True
    assert result.interest_key == "pokemon_tcg_sealed"

    assert match_product_preferences(
        "Pokemon Pikachu plush toy",
        interests=("pokemon_tcg_sealed",),
    ).matched is False
    assert match_product_preferences(
        "Pokemon trading card binder with sleeves",
        interests=("pokemon_tcg_sealed",),
    ).matched is False


def test_needoh_is_specific_instead_of_all_toys() -> None:
    assert match_product_preferences(
        "Schylling NeeDoh Nice Cube sensory fidget toy",
        interests=("needoh",),
    ).matched is True
    assert match_product_preferences(
        "Mattel Hot Wheels basic die-cast car",
        interests=("needoh",),
    ).matched is False


def test_product_aware_dm_badges_are_two_badges_and_idempotent() -> None:
    card = _card(
        "Versace Eros Eau de Parfum EDP 3.4 oz",
        brand="Versace",
        category="Fragrance",
        discount=75.0,
    )
    first = personalize_dm_card_title(card)
    second = personalize_dm_card_title(card)

    assert first == second
    assert first.startswith("💸 🧴 75% OFF")
    assert first.split()[0:2] == ["💸", "🧴"]


def test_product_aware_badges_make_sample_categories_visually_distinct() -> None:
    auto = _card("Denso Engine Oil Filter", category="Motor Oil", discount=59.0)
    grocery = _card("Hot Chili Con Carne with Beans", category="Grocery", discount=64.0)
    baby = _card("Carter's Baby Boy Outfit Set", category="Baby Clothing", discount=81.0)

    assert personalize_dm_card_title(auto).startswith("💎 🚗")
    assert personalize_dm_card_title(grocery).startswith("💎 🛒")
    assert personalize_dm_card_title(baby).startswith("🔥 👶")


def test_no_specific_product_settings_preserve_existing_matching_behavior() -> None:
    pref = _preference(categories=())
    card = _card("Ordinary exact Walmart deal", category="Home")
    assert match_dm_deal(pref, card).matched is True
