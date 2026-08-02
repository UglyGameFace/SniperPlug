from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    normalize_categories,
)
from sniperplug.services.dm_deal_matching import match_dm_deal


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


def test_smart_exact_value_deal_matches() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=35,
        min_score=78,
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


def test_keyword_category_and_exclusion_filters_are_all_enforced() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="custom",
        min_discount=30,
        min_score=50,
        categories=("apple",),
        keywords=("airpods",),
        exclude_keywords=("refurbished",),
    )

    assert match_dm_deal(preference, _card()).matched is True
    assert match_dm_deal(
        preference,
        _card(title="Refurbished Apple AirPods Pro 2nd Gen"),
    ).matched is False
    assert match_dm_deal(
        preference,
        _card(title="Samsung Galaxy Buds Pro"),
    ).matched is False


def test_walmart_cash_only_requires_strict_api_proof() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="all",
        min_discount=30,
        min_score=50,
        walmart_cash_only=True,
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


def test_smart_filter_rejects_cheap_low_savings_noise() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=35,
        min_score=78,
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
    assert decision.required_discount == 50


def test_smart_mode_never_lowers_explicit_discount_or_score_floors() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=70,
        min_score=180,
    )

    below_discount = match_dm_deal(
        preference,
        _card(discount=69.0, score=250),
    )
    below_score = match_dm_deal(
        preference,
        _card(discount=70.0, score=179),
    )

    assert below_discount.matched is False
    assert below_discount.required_discount == 70
    assert below_score.matched is False
    assert "score 179 is below the required 180" in below_score.reason


def test_walmart_cash_cannot_soften_below_user_discount_floor() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=60,
        min_score=78,
    )
    card = _card(
        discount=59.0,
        attrs={
            "walmartCashApiProof": "yes",
            "walmartCashAmount": 10,
        },
    )

    decision = match_dm_deal(preference, card)

    assert decision.matched is False
    assert decision.required_discount == 60


def test_high_markdown_cannot_bypass_explicit_savings_floor() -> None:
    preference = DmDealAlertPreference(
        user_id=1,
        enabled=True,
        mode="smart",
        min_discount=50,
        min_score=78,
        min_savings_cents=20000,
    )

    decision = match_dm_deal(
        preference,
        _card(current=20.0, reference=100.0, discount=80.0, score=200),
    )

    assert decision.matched is False
    assert "below the required $200.00" in decision.reason


def test_category_aliases_expand_without_duplicates() -> None:
    categories = normalize_categories("tech, apple, cash, tech")

    assert "apple" in categories
    assert "gpus" in categories
    assert "walmart_cash" in categories
    assert len(categories) == len(set(categories))
