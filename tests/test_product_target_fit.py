from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.deal_search_modes import (
    MODE_BEST, best_pick_card_score, popular_card_score, product_quality_score,
    rank_for_search_mode,
)
from sniperplug.services.opportunity_watchlist import category_for_title
from sniperplug.services.product_target_fit import assess_product_target_fit
from sniperplug.services.walmart_exact_price_enrichment import _enrichment_priority


@pytest.mark.parametrize("title,family", [
    ("Sony PlayStation 5 PS5 Console 1TB", "game_console"),
    ("Xbox Series X Console Bundle with Extra Controller", "game_console"),
    ("Nintendo Switch 2 System", "game_console"),
    ("Sony DualSense Wireless Controller for PS5", "game_controller"),
    ("Xbox Wireless Controller Carbon Black", "game_controller"),
    ("NVIDIA GeForce RTX 5090 Graphics Card", "graphics_card"),
    ("ASUS 27 inch 4K Gaming Monitor", "gaming_monitor"),
    ("Samsung 65 inch 4K Smart TV", "television"),
    ("Apple MacBook Pro M4", "laptop"),
    ("Apple iPhone 16 Pro Max Unlocked", "phone"),
    ("Apple iPad Pro 13 inch", "tablet"),
    ("Meta Quest 3 VR Headset", "vr_headset"),
])
def test_complete_products_get_curated_interest_only(title, family):
    fit = assess_product_target_fit(SimpleNamespace(title=title))
    assert fit.tier == "curated_target"
    assert fit.family == family
    assert fit.rank_points > 0
    assert "not measured" in fit.reason


@pytest.mark.parametrize("title", [
    "PS5 Console Protective Carry Case",
    "Xbox Series X Replacement Cooling Fan",
    "Nintendo Switch 2 Screen Protector Tempered Glass",
    "Sony DualSense Controller Charging Stand",
    "RTX 5090 Protective Case",
    "LG OLED TV Mount Bracket",
    "Samsung 27 inch Gaming Monitor Stand",
    "MacBook Pro Protective Sleeve",
    "iPhone 16 Pro Max Silicone Case",
    "Apple iPad Pro Screen Protector",
    "Meta Quest 3 Carry Bag",
])
def test_premium_product_name_does_not_make_accessory_sought_after_hardware(title):
    fit = assess_product_target_fit(SimpleNamespace(title=title))
    assert fit.tier == "accessory"
    assert fit.rank_points < 0


def test_unknown_title_remains_unknown_instead_of_fake_sales_demand():
    fit = assess_product_target_fit(SimpleNamespace(title="Generic discount item"))
    assert fit.tier == "unclassified"
    assert fit.rank_points == 0


def _card(label, *, body="Verified Walmart product", rating_attrs=None):
    return SimpleNamespace(
        retailer="walmart", label=label, discount=65.0, score=105,
        current_price=30.0, should_alert=True,
        variant_attributes=rating_attrs or {},
        embed=discord.Embed(title=label, description=body),
    )


def test_best_picks_and_popular_brands_rank_console_over_case():
    console = _card("PlayStation 5 PS5 Console 1TB")
    cover = _card("PlayStation 5 PS5 Console Protective Case")
    assert best_pick_card_score(console)[0] > best_pick_card_score(cover)[0]
    assert popular_card_score(console)[0] > popular_card_score(cover)[0]
    assert rank_for_search_mode([cover, console], [], MODE_BEST).verified[0] is console


def test_rendered_rating_claims_cannot_raise_quality_or_demand_scores():
    ordinary = _card("Generic Storage Box")
    invented = _card(
        "Generic Storage Box",
        body="Rating: 5.0, 999999 reviews. Best seller; buy now",
    )
    assert product_quality_score(ordinary) == product_quality_score(invented)
    official = _card("Generic Storage Box", rating_attrs={"rating": "4.8", "reviews": "1000"})
    assert product_quality_score(official) > product_quality_score(ordinary)


def test_exact_queue_boost_is_bounded_and_accessories_still_eligible_for_verification():
    def candidate(title):
        return SourceCandidate(
            source_key="walmart", retailer="Walmart",
            title=title, product_url="https://www.walmart.com/ip/123456",
            current_price=7.00, api_current_price=7.00,
            product_id="123456",
        )
    gpu = _enrichment_priority(candidate("NVIDIA GeForce RTX 5090 Graphics Card"), min_discount=40)
    case = _enrichment_priority(candidate("NVIDIA GeForce RTX 5090 Graphics Card Case"), min_discount=40)
    assert gpu > case
    assert case > 0
    # The older, intentional +400 extreme-device priority remains intact for
    # actual hardware; this is the point of beating cheap protective cases.
    assert gpu - case >= 400


def test_watch_categories_match_whole_terms_not_accidental_fragments():
    assert category_for_title("Algae Extract") is None
    assert category_for_title("Algae Aquarium Decorations").key == "pet_supplies"
    assert category_for_title("Apple iPhone 16") is not None
    assert category_for_title("Laptop Sleeve") is None or category_for_title("Laptop Sleeve").key != "apple"
