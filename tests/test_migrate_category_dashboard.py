from pathlib import Path

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_category_preferences import apply_category_preferences, category_for_card


PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
AUTOSCAN = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_only_one_category_slash_command_remains():
    assert 'name="deal_categories"' in PUBLIC_ALERTS
    assert 'name="deal_category"' not in PUBLIC_ALERTS


def test_dashboard_replaces_text_command_with_buttons():
    assert "class DealCategoryDashboardView" in PUBLIC_ALERTS
    assert "DealCategorySelect" in PUBLIC_ALERTS
    assert "DealCategoryPresetButton" in PUBLIC_ALERTS
    assert "Deal Week" in PUBLIC_ALERTS
    assert "Flip Focus" in PUBLIC_ALERTS
    assert "Essentials" in PUBLIC_ALERTS


def test_autoscan_applies_category_preferences():
    assert "apply_category_preferences" in AUTOSCAN
    apply_index = AUTOSCAN.index("apply_category_preferences")
    post_index = AUTOSCAN.index("maybe_post_public_deal_cards", apply_index)
    assert apply_index < post_index


def test_mobile_accessory_extreme_deal_breaks_through_muted_category():
    card = DealCard(
        embed=discord.Embed(title="3 in 1 Wireless Charging Station for iPhone Apple Watch AirPods"),
        url="https://www.walmart.com/ip/123",
        label="3 in 1 Wireless Charging Station for iPhone Apple Watch AirPods",
        score=120,
        discount=89,
    )

    category = category_for_card(card)
    allowed, suppressed, notes = apply_category_preferences([card], {"mobile_accessories": "muted"})

    assert category is not None
    assert category.key == "mobile_accessories"
    assert card in allowed
    assert not suppressed
    assert any("override" in note.lower() for note in notes)
