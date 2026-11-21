from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    UNLIMITED_MAX_ALERTS_PER_DAY,
    daily_cap_label,
    normalize_categories,
)
from sniperplug.services.dm_deal_matching import DM_INTERNAL_MIN_SCORE, match_dm_deal
from sniperplug.services.dm_personal_categories import split_category_preferences


def _card(
    *,
    title: str = "Apple AirPods Pro 2nd Gen",
    current: float | None = 99.0,
    reference: float | None = 249.0,
    discount: float | None = 60.2,
    score: int = 120,
    attrs: dict | None = None,
):
    embed = discord.Embed(title=f"🔥 {discount or 0:.0f}% OFF • {title}")
    embed.add_field(name="Product Proof", value=f"Brand: {title.split()[0]}")
    return SimpleNamespace(
        label=title,
        url="https://www.walmart.com/ip/123",
        embed=embed,
        api_current_price=current,
        current_price=current,
        api_reference_price=reference,
        typical_price=reference,
        api_discount_percent=discount,
        discount=discount,
        score=score,
        variant_attributes=dict(attrs or {}),
    )


def test_exact_value_deal_matches_with_no_picks() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=35,
    )

    decision = match_dm_deal(preference, _card())

    assert decision.matched is True
    assert decision.category_key == "apple"
    assert decision.savings_cents == 15000
    assert "exact markdown" in decision.reason


def test_missing_exact_reference_fails_closed() -> None:
    preference = DmDealAlertPreference(user_id=1, enabled=True)

    decision = match_dm_deal(preference, _card(reference=None))

    assert decision.matched is False
    assert "exact current/was price proof" in decision.reason


def test_selected_category_is_an_inclusion_layer() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("apple",),
    )

    assert match_dm_deal(preference, _card()).matched is True
    assert match_dm_deal(
        preference,
        _card(title="Samsung Galaxy Buds Pro"),
    ).matched is False


def test_walmart_cash_category_matches_only_with_strict_api_proof() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("walmart_cash",),
    )

    without_proof = match_dm_deal(
        preference,
        _card(attrs={"walmartCashAmount": 10}),
    )
    with_proof = match_dm_deal(
        preference,
        _card(
            attrs={
                "walmartCashApiProof": "yes",
                "walmartCashAmount": 10,
            }
        ),
    )

    assert without_proof.matched is False
    assert with_proof.matched is True
    assert with_proof.walmart_cash_cents == 1000


def test_discount_floor_is_exactly_the_users_floor() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=35,
    )

    decision = match_dm_deal(
        preference,
        _card(
            title="Household Soap",
            current=8.0,
            reference=11.0,
            discount=27.27,
            score=100,
        ),
    )

    assert decision.matched is False
    assert decision.required_discount == 35


def test_internal_score_floor_is_a_constant_not_a_user_setting() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=20,
    )

    below_score = match_dm_deal(
        preference,
        _card(discount=80.0, score=DM_INTERNAL_MIN_SCORE - 1),
    )
    at_score = match_dm_deal(
        preference,
        _card(discount=80.0, score=DM_INTERNAL_MIN_SCORE),
    )

    assert below_score.matched is False
    assert (
        f"score {DM_INTERNAL_MIN_SCORE - 1} is below the required "
        f"{DM_INTERNAL_MIN_SCORE}"
    ) in below_score.reason
    assert at_score.matched is True


def test_savings_are_reported_but_never_a_floor() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=50,
    )

    decision = match_dm_deal(
        preference,
        _card(current=20.0, reference=100.0, discount=80.0, score=200),
    )

    assert decision.matched is True
    assert decision.savings_cents == 8000


def test_favorites_drop_on_read_instead_of_becoming_subscriptions() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        categories=("favorite:gpus", "favorite:smart_home", "apple"),
    ).normalized()

    assert "favorite:gpus" not in preference.categories
    assert preference.categories == ("apple",)
    assert split_category_preferences(preference.categories) == ("apple",)


def test_summary_sentence_counts_only_real_selected_categories() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=50,
        categories=(
            "gpus",
            "favorite:shoes_apparel",
            "favorite:appliances",
            "favorite:smart_home",
        ),
    )

    assert preference.summary_sentence() == (
        "You'll get DMs for 1 category you picked · 50% off or "
        "better · up to 25 a day."
    )

    favorite_only = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=50,
        categories=("favorite:shoes_apparel",),
    )
    assert "all deals" in favorite_only.summary_sentence()


def test_muted_tokens_drop_silently_on_read() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        min_discount=30,
        categories=("muted:baby_kids",),
    )

    # The mute is gone, so the preference has no picks at all and a baby
    # deal is just an ordinary eligible deal again.
    assert preference.normalized().categories == ()
    decision = match_dm_deal(preference, _card(title="Gerber Baby Boys Pajama Set"))
    assert decision.matched is True


def test_summary_sentence_describes_the_plan_in_one_line() -> None:
    preference = DmDealAlertPreference(
        user_id=629459300854661120,
        enabled=True,
        min_discount=50,
        max_alerts_per_day=UNLIMITED_MAX_ALERTS_PER_DAY,
        categories=(
            "dm_route:price_glitch",
            "dm_route:hot_deals",
            "interest:laptops",
            "flip_profit:3000",
            "gpus",
        ),
    )

    sentence = preference.summary_sentence()

    assert sentence == (
        "You'll get DMs for price mistakes, big markdowns, specific "
        "products you picked and 1 category you picked · 50% off or "
        "better · price mistakes worth $30+ · no daily limit."
    )


def test_summary_sentence_for_broad_and_disabled_plans() -> None:
    broad = DmDealAlertPreference(user_id=1, enabled=True, min_discount=35)
    assert broad.summary_sentence() == (
        "You'll get DMs for all deals · 35% off or better · up to 25 a day."
    )

    disabled = DmDealAlertPreference(user_id=1, enabled=False, min_discount=35)
    assert disabled.summary_sentence().startswith("Deal DMs are off")


def test_category_aliases_expand_without_duplicates() -> None:
    categories = normalize_categories("tech, apple, cash, tech")

    assert "apple" in categories
    assert "gpus" in categories
    assert "walmart_cash" in categories
    assert len(categories) == len(set(categories))


def test_unlimited_daily_cap_survives_normalization_and_displays_cleanly() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        max_alerts_per_day=UNLIMITED_MAX_ALERTS_PER_DAY,
    ).normalized()

    assert preference.max_alerts_per_day == UNLIMITED_MAX_ALERTS_PER_DAY
    assert daily_cap_label(preference.max_alerts_per_day) == "unlimited"
    assert any("unlimited" in line.lower() for line in preference.summary_lines())


def test_finite_daily_cap_still_clamps_to_supported_range() -> None:
    assert DmDealAlertPreference(user_id=1, max_alerts_per_day=101).normalized().max_alerts_per_day == 100
    assert DmDealAlertPreference(user_id=1, max_alerts_per_day=-5).normalized().max_alerts_per_day == 1
