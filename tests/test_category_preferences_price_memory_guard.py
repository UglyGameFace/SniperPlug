import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_category_preferences import (
    CATEGORY_MODE_MUTED,
    apply_category_preferences,
    category_for_card,
    decide_category,
)
from sniperplug.services.public_deal_quality import LANE_PRICE_MEMORY_DROP


def price_memory_card(title: str = "Hyper Tough Tire Inflator") -> DealCard:
    embed = discord.Embed(
        title=f"📉 Walmart price drop • {title}",
        description="Same exact Walmart item is lower than prior API price. Walmart Cash, OnePay, marketplace comps, and search words were not used as proof.",
    )
    embed.add_field(
        name="✅ Observed price-drop proof",
        value="Previous observed API price: **$60.00**\nCurrent API price: **$20.00**\nObserved drop: **67%**",
        inline=False,
    )
    card = DealCard(
        embed=embed,
        url="https://www.walmart.com/ip/123",
        label=title,
        score=100,
        discount=67,
        deal_lane=LANE_PRICE_MEMORY_DROP,
        api_current_price=20.0,
        api_reference_price=60.0,
        api_discount_percent=67,
        direct_product_url="https://www.walmart.com/ip/123",
        variant_attributes={
            "priceMemoryIdentity": "walmart:123",
            "referencePriceTrusted": "yes",
        },
    )
    return card


def test_price_memory_safety_copy_does_not_reclassify_as_walmart_cash():
    card = price_memory_card()
    category = category_for_card(card)

    assert category is None or category.key != "walmart_cash"


def test_muted_walmart_cash_preference_does_not_suppress_price_memory_deal():
    card = price_memory_card()
    allowed, suppressed, notes = apply_category_preferences([card], {"walmart_cash": CATEGORY_MODE_MUTED})

    assert allowed == [card]
    assert suppressed == []
    assert getattr(card, "deal_category_key") != "walmart_cash"


def test_strong_observed_price_memory_breaks_through_muted_matching_category():
    card = price_memory_card("Hyper Tough Tire Inflator Portable Air Compressor")
    category = category_for_card(card)
    assert category is not None

    decision = decide_category(card, {category.key: CATEGORY_MODE_MUTED})

    assert decision.action == "allow_extreme"
    assert "observed price-memory proof" in decision.reason


def test_normal_non_memory_card_can_still_be_suppressed_by_preferences():
    card = DealCard(
        embed=discord.Embed(title="Normal tire inflator lead"),
        url="https://www.walmart.com/ip/456",
        label="Hyper Tough Tire Inflator",
        score=50,
        discount=20,
    )
    category = category_for_card(card)
    assert category is not None

    allowed, suppressed, _notes = apply_category_preferences([card], {category.key: CATEGORY_MODE_MUTED})

    assert allowed == []
    assert suppressed == [card]
