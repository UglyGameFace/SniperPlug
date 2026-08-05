from pathlib import Path


SOURCE = Path("sniperplug/cogs/dm_deal_alerts.py").read_text(encoding="utf-8")
MATCHER = Path("sniperplug/services/dm_deal_matching.py").read_text(encoding="utf-8")
PERSONAL = Path("sniperplug/services/dm_personal_categories.py").read_text(encoding="utf-8")
FLIP = Path("sniperplug/services/dm_flip_opportunities.py").read_text(encoding="utf-8")


def test_dm_deals_exposes_category_mute_and_restore_options() -> None:
    assert 'mute_categories="Hide categories from normal DMs' in SOURCE
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


def test_dm_deals_exposes_strict_flip_controls() -> None:
    assert 'flip_alerts="Allow exceptional price-error/resell alerts' in SOURCE
    assert 'flip_min_profit="Minimum conservative estimated net profit' in SOURCE
    assert "flip_alerts: bool | None" in SOURCE
    assert "flip_min_profit: app_commands.Range[float, 10.0, 10000.0] | None" in SOURCE
    assert "update_flip_settings(" in SOURCE
    assert 'FLIP_ALERTS_TOKEN = "flip:enabled"' in PERSONAL
    assert 'FLIP_MIN_PROFIT_PREFIX = "flip_profit:"' in PERSONAL


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


def test_flip_lane_never_treats_active_listings_as_sold_proof() -> None:
    assert "ebayRecentSoldCount" in FLIP
    assert "ebayMedianSoldPrice" in FLIP
    assert "ebayCompIdentityMatched" in FLIP
    assert "ebayCompConditionMatched" in FLIP
    assert "ebayActiveListing" not in FLIP
    assert "recent sold comps not connected" in FLIP
    assert "assess_flip_opportunity(" in MATCHER
    assert "if flip.qualified" in MATCHER


def test_flip_lane_keeps_hard_personal_limits_before_override() -> None:
    max_price_position = MATCHER.index("if pref.max_price_cents is not None")
    exclusion_position = MATCHER.index("if keyword_excludes and any")
    flip_position = MATCHER.index("if flip_enabled:")
    mute_position = MATCHER.index("if category_key in muted_categories")

    assert max_price_position < flip_position
    assert exclusion_position < flip_position
    assert flip_position < mute_position
