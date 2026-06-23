from pathlib import Path

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.deal_category_preferences import (
    apply_category_preferences,
    category_for_card,
    category_group_count,
    dashboard_quick_state,
    format_category_group_page,
    summarize_category_preferences,
)


PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
ROUTES = Path("sniperplug/services/verified_discount_hunt.py").read_text(encoding="utf-8")


def test_dashboard_has_readable_grouped_sections_and_cash_preset():
    assert "🎛️ Deal Feed Controls" in PUBLIC_ALERTS
    assert "Currently in use" in PUBLIC_ALERTS
    assert "Categories in this section" in PUBLIC_ALERTS
    assert "Walmart Cash" in PUBLIC_ALERTS
    assert "DealCategoriesShortcutView" in PUBLIC_ALERTS


def test_walmart_cash_routes_exist():
    assert "walmart cash eligible" in ROUTES
    assert "walmart cash offers" in ROUTES


def test_group_pages_render_clear_on_normal_muted_states():
    text = format_category_group_page({"walmart_cash": "priority", "open_box_restored": "muted"}, page=0)

    assert "⭐ ON" in text
    assert "🙈 MUTED" in text
    assert "Walmart Cash Offers" in text
    assert category_group_count() >= 5


def test_summary_uses_labels_not_raw_key_wall():
    text = summarize_category_preferences({"walmart_cash": "priority", "pet_supplies": "muted"})

    assert "Walmart Cash Offers" in text
    assert "Pet Supplies" in text
    assert "`walmart_cash`" not in text
    assert dashboard_quick_state({"walmart_cash": "priority", "pet_supplies": "muted"}).startswith("⭐ Priority ON")


def test_walmart_cash_card_classifies_from_embed_field():
    embed = discord.Embed(title="Beauty deal")
    embed.add_field(name="Walmart Cash Offers", value="Walmart Cash eligible", inline=False)
    card = DealCard(embed=embed, url="https://www.walmart.com/ip/123", label="Beauty deal", score=80, discount=20)

    category = category_for_card(card)

    assert category is not None
    assert category.key == "walmart_cash"


def test_muted_still_does_not_block_extreme_cash_or_charger_deal():
    card = DealCard(
        embed=discord.Embed(title="3 in 1 Wireless Charging Station for iPhone Apple Watch AirPods"),
        url="https://www.walmart.com/ip/123",
        label="3 in 1 Wireless Charging Station for iPhone Apple Watch AirPods",
        score=120,
        discount=89,
    )
    allowed, suppressed, notes = apply_category_preferences([card], {"mobile_accessories": "muted"})

    assert card in allowed
    assert not suppressed
    assert any("override" in note.lower() for note in notes)
