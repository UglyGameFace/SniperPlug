from pathlib import Path

PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
BOT = Path("sniperplug/bot.py").read_text(encoding="utf-8")


def test_deal_category_panels_use_timeout_none():
    dashboard_start = PUBLIC_ALERTS.index("class DealCategoryDashboardView")
    shortcut_start = PUBLIC_ALERTS.index("class DealCategoriesShortcutView")
    cog_start = PUBLIC_ALERTS.index("class PublicAlertsCog")

    dashboard_block = PUBLIC_ALERTS[dashboard_start:shortcut_start]
    shortcut_block = PUBLIC_ALERTS[shortcut_start:cog_start]

    assert "super().__init__(timeout=None)" in dashboard_block
    assert "super().__init__(timeout=None)" in shortcut_block
    assert "timeout=300" not in dashboard_block
    assert "timeout=300" not in shortcut_block


def test_public_panel_button_has_static_custom_id_for_restart_recovery():
    assert "DEAL_CATEGORIES_OPEN_CUSTOM_ID" in PUBLIC_ALERTS
    assert "sniperplug:panel:deal_categories:open" in PUBLIC_ALERTS
    assert "custom_id=DEAL_CATEGORIES_OPEN_CUSTOM_ID" in PUBLIC_ALERTS


def test_public_panel_opens_fresh_live_dashboard_from_interaction_context():
    button_start = PUBLIC_ALERTS.index("class OpenDealCategoriesButton")
    shortcut_start = PUBLIC_ALERTS.index("class DealCategoriesShortcutView")
    block = PUBLIC_ALERTS[button_start:shortcut_start]

    assert "interaction.guild_id" in block
    assert "getattr(interaction.client, \"db\", None)" in block
    assert "get_category_preferences(db, guild_id)" in block
    assert "DealCategoryDashboardView(db, guild_id, preferences)" in block


def test_bot_registers_persistent_public_panel_views_on_boot():
    assert "register_persistent_public_panel_views" in BOT
    assert "Persistent public panel views registered" in BOT


def test_panel_copy_no_longer_says_it_expires():
    assert "Private dashboard expires after a few minutes" not in PUBLIC_ALERTS
    assert "Buttons do not expire" in PUBLIC_ALERTS
    assert "reopen fresh settings every time" in PUBLIC_ALERTS
