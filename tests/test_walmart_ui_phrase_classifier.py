import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_category_preferences import category_for_card
from sniperplug.services.walmart_ui_phrases import (
    find_walmart_cash_ui_offer,
    find_walmart_condition_ui_signal,
    find_walmart_markdown_ui_signal,
    find_walmart_popularity_ui_signal,
)


def test_actual_walmart_cash_grid_wording_is_detected():
    text = "$27.24 Tide Pods Laundry Detergent Get $4.00 Walmart Cash Manufacturer offer Pickup 1-day shipping"
    match = find_walmart_cash_ui_offer(text)

    assert match is not None
    assert match.kind == "walmart_cash"
    assert "Get $4.00 Walmart Cash" in match.phrase


def test_actual_walmart_cash_pdp_wording_is_detected():
    text = "$3.48 Price when purchased online Get $3.50 Walmart Cash Manufacturer offer Add to cart"
    match = find_walmart_cash_ui_offer(text)

    assert match is not None
    assert match.kind == "walmart_cash"
    assert "Walmart Cash" in match.phrase


def test_walmart_cash_available_badge_is_candidate_only_wording():
    text = "Walmart Cash available on select options of this item"
    match = find_walmart_cash_ui_offer(text)

    assert match is not None
    assert match.kind == "walmart_cash_badge"


def test_rollback_now_you_save_words_are_markdown_ui_not_cash():
    text = "Rollback Now $98.00 $144.00 You save $46.00 Price when purchased online"

    assert find_walmart_markdown_ui_signal(text) is not None
    assert find_walmart_cash_ui_offer(text) is None


def test_walmart_popularity_words_are_not_deal_proof():
    text = "1000+ bought since yesterday In 200+ people's carts Best seller Popular pick"
    match = find_walmart_popularity_ui_signal(text)

    assert match is not None
    assert match.kind == "popularity_ui"
    assert find_walmart_cash_ui_offer(text) is None
    assert find_walmart_markdown_ui_signal(text) is None


def test_restored_like_new_good_fair_words_are_condition_ui():
    assert find_walmart_condition_ui_signal("Restored: Like New") is not None
    assert find_walmart_condition_ui_signal("Restored: Good") is not None
    assert find_walmart_condition_ui_signal("Restored: Fair") is not None
    assert find_walmart_condition_ui_signal("Open-box excellent condition") is not None


def test_category_detection_catches_actual_cash_ui_words():
    embed = discord.Embed(
        title="Tide Pods Laundry Detergent",
        description="Get $4.00 Walmart Cash Manufacturer offer Pickup 1-day shipping",
    )
    card = DealCard(embed=embed, url="https://www.walmart.com/ip/123", label="Tide Pods Laundry Detergent", score=0, discount=0)

    category = category_for_card(card)

    assert category is not None
    assert category.key == "walmart_cash"


def test_category_detection_catches_actual_restored_ui_words():
    embed = discord.Embed(title="Samsung Galaxy Tablet Restored: Like New")
    card = DealCard(embed=embed, url="https://www.walmart.com/ip/456", label="Samsung Galaxy Tablet Restored: Like New", score=0, discount=0)

    category = category_for_card(card)

    assert category is not None
    assert category.key == "open_box_restored"
