from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.cogs.dm_deal_alerts import build_dm_settings_embed
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.dm_personal_categories import (
    flip_settings,
    split_category_preferences,
    update_flip_settings,
)


def _card(
    title: str,
    *,
    current: float,
    reference: float,
    discount: float,
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


def _flip_preference(
    *,
    max_price_cents: int | None = None,
    exclude_keywords: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    minimum_profit_cents: int = 5_000,
    min_discount: int = 35,
    min_score: int = 78,
    min_savings_cents: int = 0,
) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=min_discount,
        min_score=min_score,
        min_savings_cents=min_savings_cents,
        max_price_cents=max_price_cents,
        categories=(
            "favorite:gpus",
            "muted:baby_kids",
            "flip:enabled",
            f"flip_profit:{minimum_profit_cents}",
        ),
        keywords=keywords,
        exclude_keywords=exclude_keywords,
    )


def test_cheap_baby_clothing_stays_muted_despite_large_percentage() -> None:
    decision = match_dm_deal(
        _flip_preference(),
        _card(
            "Gerber Baby Girls Bodysuit Set",
            current=10.0,
            reference=100.0,
            discount=90.0,
        ),
    )

    assert decision.matched is False
    assert decision.reason == "category is muted in your personal DMs"


def test_significant_cross_category_price_error_can_break_mute() -> None:
    decision = match_dm_deal(
        _flip_preference(),
        _card(
            "Graco Baby Stroller Travel System",
            current=50.0,
            reference=300.0,
            discount=83.3,
            score=125,
        ),
    )

    assert decision.matched is True
    assert decision.category_key == "baby_kids"
    assert "Price-error / flip override" in decision.reason
    assert "conservative estimated net" in decision.reason
    assert "sold comps not connected" in decision.reason


def test_flip_override_respects_hard_maximum_price() -> None:
    decision = match_dm_deal(
        _flip_preference(max_price_cents=4_000),
        _card(
            "Graco Baby Stroller Travel System",
            current=50.0,
            reference=300.0,
            discount=83.3,
            score=125,
        ),
    )

    assert decision.matched is False
    assert decision.reason == "price is above your maximum"


def test_flip_override_respects_explicit_excluded_words() -> None:
    decision = match_dm_deal(
        _flip_preference(exclude_keywords=("refurbished",)),
        _card(
            "Refurbished Graco Baby Stroller Travel System",
            current=50.0,
            reference=300.0,
            discount=83.3,
            score=125,
        ),
    )

    assert decision.matched is False
    assert decision.reason == "an excluded keyword matched"


def test_flip_override_respects_required_keywords() -> None:
    decision = match_dm_deal(
        _flip_preference(keywords=("electronics",)),
        _card(
            "Graco Baby Stroller Travel System",
            current=50.0,
            reference=300.0,
            discount=83.3,
            score=125,
        ),
    )

    assert decision.matched is False
    assert decision.reason == "none of your required keywords matched"


def test_flip_override_respects_explicit_score_and_savings_floors() -> None:
    low_score = match_dm_deal(
        _flip_preference(min_score=140),
        _card(
            "Graco Baby Stroller Travel System",
            current=50.0,
            reference=300.0,
            discount=83.3,
            score=125,
        ),
    )
    low_savings = match_dm_deal(
        _flip_preference(min_savings_cents=30_000),
        _card(
            "Graco Baby Stroller Travel System",
            current=50.0,
            reference=300.0,
            discount=83.3,
            score=125,
        ),
    )

    assert low_score.matched is False
    assert low_score.reason == "score 125 is below the required 140"
    assert low_savings.matched is False
    assert "below the required $300.00" in low_savings.reason


def test_exact_recent_ebay_sold_comps_can_confirm_any_category() -> None:
    decision = match_dm_deal(
        _flip_preference(),
        _card(
            "Premium Baby Carrier",
            current=20.0,
            reference=100.0,
            discount=80.0,
            score=100,
            attrs={
                "department": "Baby Gear",
                "ebayCompIdentityMatched": "yes",
                "ebayCompConditionMatched": "yes",
                "ebayRecentSoldCount": 7,
                "ebaySoldWindowDays": 30,
                "ebayMedianSoldPrice": 150.0,
            },
        ),
    )

    assert decision.matched is True
    assert decision.category_key == "baby_kids"
    assert "eBay sold-comp flip" in decision.reason
    assert "7 sold in 30d" in decision.reason
    assert "median $150.00" in decision.reason


def test_active_ebay_listing_prices_never_count_as_sold_comps() -> None:
    decision = match_dm_deal(
        _flip_preference(),
        _card(
            "Gerber Baby Girls Bodysuit Set",
            current=10.0,
            reference=100.0,
            discount=90.0,
            attrs={
                "ebayActiveListingCount": 500,
                "ebayActiveListingMedianPrice": 150.0,
            },
        ),
    )

    assert decision.matched is False
    assert decision.reason == "category is muted in your personal DMs"


def test_mismatched_or_stale_ebay_comps_cannot_override_mute() -> None:
    for attrs in (
        {
            "department": "Baby Gear",
            "ebayCompIdentityMatched": "no",
            "ebayCompConditionMatched": "yes",
            "ebayRecentSoldCount": 10,
            "ebaySoldWindowDays": 30,
            "ebayMedianSoldPrice": 200.0,
        },
        {
            "department": "Baby Gear",
            "ebayCompIdentityMatched": "yes",
            "ebayCompConditionMatched": "yes",
            "ebayRecentSoldCount": 10,
            "ebaySoldWindowDays": 180,
            "ebayMedianSoldPrice": 200.0,
        },
    ):
        decision = match_dm_deal(
            _flip_preference(minimum_profit_cents=15_000),
            _card(
                "Premium Baby Carrier",
                current=20.0,
                reference=100.0,
                discount=80.0,
                score=100,
                attrs=attrs,
            ),
        )
        assert decision.matched is False
        assert decision.reason == "category is muted in your personal DMs"


def test_flip_settings_preserve_favorites_and_hard_allowlist() -> None:
    categories = update_flip_settings(
        ("apple", "favorite:gpus", "muted:baby_kids"),
        enabled=True,
        minimum_profit_cents=7_500,
    )
    selected, favorites = split_category_preferences(categories)
    enabled, minimum_profit = flip_settings(categories)

    assert selected == ("apple",)
    assert favorites == ("gpus",)
    assert "muted:baby_kids" in categories
    assert enabled is True
    assert minimum_profit == 7_500

    disabled = update_flip_settings(categories, enabled=False)
    selected, favorites = split_category_preferences(disabled)
    enabled, minimum_profit = flip_settings(disabled)
    assert selected == ("apple",)
    assert favorites == ("gpus",)
    assert "muted:baby_kids" in disabled
    assert enabled is False
    assert minimum_profit == 7_500


def test_settings_show_flip_state_without_internal_tokens() -> None:
    preference = _flip_preference(minimum_profit_cents=7_500)
    embed = build_dm_settings_embed(preference)
    description = embed.description or ""

    assert "Price-error / flip override: **enabled**" in description
    assert "Minimum estimated flip profit: **$75.00**" in description
    assert "flip:enabled" not in description
    assert "flip_profit:7500" not in description
