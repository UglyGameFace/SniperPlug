import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_category_preferences import (
    apply_category_preferences,
    category_for_card,
    decide_category,
    format_category_catalog,
    normalize_category_mode,
)


def charger_card(discount=89, score=120):
    return DealCard(
        embed=discord.Embed(title="3 in 1 Wireless Charging Station for iPhone Apple Watch AirPods"),
        url="https://www.walmart.com/ip/123",
        label="3 in 1 Wireless Charging Station for iPhone Apple Watch AirPods",
        score=score,
        discount=discount,
    )


def test_mobile_charger_categorizes_correctly():
    category = category_for_card(charger_card())

    assert category is not None
    assert category.key == "mobile_accessories"


def test_popular_is_boost_not_required():
    card = charger_card(discount=40, score=80)
    decision = decide_category(card, {"mobile_accessories": "priority"})

    assert decision.action == "boost"
    allowed, suppressed, notes = apply_category_preferences([card], {"mobile_accessories": "priority"})

    assert allowed == [card]
    assert not suppressed
    assert card.score >= 105
    assert any("Priority category" in note for note in notes)


def test_muted_category_hides_normal_but_not_extreme():
    normal = charger_card(discount=35, score=80)
    extreme = charger_card(discount=89, score=120)

    allowed, suppressed, notes = apply_category_preferences([normal, extreme], {"mobile_accessories": "muted"})

    assert normal in suppressed
    assert extreme in allowed
    assert any("Muted category override" in note for note in notes)


def test_unknown_category_is_not_blocked():
    card = DealCard(embed=discord.Embed(title="Very Weird Product Name"), url="u", label="Very Weird Product Name", score=120, discount=80)
    decision = decide_category(card, {})

    assert decision.category_key == "unknown"
    assert decision.action == "allow"


def test_mode_aliases_and_catalog():
    assert normalize_category_mode("boost") == "priority"
    assert normalize_category_mode("hide") == "muted"
    assert normalize_category_mode("whatever") == "normal"

    catalog = format_category_catalog({"mobile_accessories": "priority"})
    assert "`mobile_accessories`" in catalog
    assert "Mobile Accessories / Chargers" in catalog
