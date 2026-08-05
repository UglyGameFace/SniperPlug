from pathlib import Path


SOURCE = Path("sniperplug/cogs/dm_deal_alerts.py").read_text(encoding="utf-8")
MATCHER = Path("sniperplug/services/dm_deal_matching.py").read_text(encoding="utf-8")
PERSONAL = Path("sniperplug/services/dm_personal_categories.py").read_text(encoding="utf-8")


def test_dm_deals_exposes_category_mute_and_restore_options() -> None:
    assert 'mute_categories="Hide categories only from your DMs' in SOURCE
    assert 'unmute_categories="Restore personally muted categories' in SOURCE
    assert "mute_categories: app_commands.Range[str, 1, 300] | None" in SOURCE
    assert "unmute_categories: app_commands.Range[str, 1, 300] | None" in SOURCE
    assert "update_category_mutes(" in SOURCE


def test_dm_deals_exposes_favorite_category_options() -> None:
    assert 'favorite_categories="Prioritize interests without excluding other great deals' in SOURCE
    assert 'unfavorite_categories="Remove categories from your personal favorites' in SOURCE
    assert "favorite_categories: app_commands.Range[str, 1, 300] | None" in SOURCE
    assert "unfavorite_categories: app_commands.Range[str, 1, 300] | None" in SOURCE
    assert "update_favorite_categories(" in SOURCE


def test_category_mutes_and_favorites_are_personal_delivery_filters() -> None:
    assert 'CATEGORY_MUTE_PREFIX = "category:"' in PERSONAL
    assert 'FAVORITE_CATEGORY_PREFIX = "favorite:"' in PERSONAL
    assert '"baby": ("baby_kids",)' in PERSONAL
    assert '"pc": ("gpus", "cpus", "ram", "ssds")' in PERSONAL
    assert '"category is muted in your personal DMs"' in MATCHER
    assert "category_key in muted_categories" in MATCHER
    assert "category_key in favorite_categories" in MATCHER
    assert "selected_categories and category_key not in selected_categories" in MATCHER
    assert "favorite-category priority" in MATCHER
