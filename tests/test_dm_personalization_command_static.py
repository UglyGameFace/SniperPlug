from pathlib import Path


SOURCE = Path("sniperplug/cogs/dm_deal_alerts.py").read_text(encoding="utf-8")
MENU = Path("sniperplug/cogs/dm_deal_preferences_view.py").read_text(encoding="utf-8")
MATCHER = Path("sniperplug/services/dm_deal_matching.py").read_text(encoding="utf-8")
PERSONAL = Path("sniperplug/services/dm_personal_categories.py").read_text(encoding="utf-8")
FLIP = Path("sniperplug/services/dm_flip_opportunities.py").read_text(encoding="utf-8")


def test_dm_deals_exposes_complete_personalization_menu() -> None:
    assert 'Choice(name="Open personalization menu", value="menu")' in SOURCE
    assert "DmDealPreferencesView(" in SOURCE
    assert "all_personal_categories()" in MENU
    assert "personal_category_pages(" in MENU
    assert "Search every deal category" in MENU
    assert "All categories" in MENU
    assert "CATEGORY_PAGE_SIZE = 25" in PERSONAL


def test_menu_has_favorite_mute_allowlist_and_flip_controls() -> None:
    assert 'TAB_FAVORITES = "favorites"' in MENU
    assert 'TAB_MUTED = "muted"' in MENU
    assert 'TAB_ALLOWLIST = "allowlist"' in MENU
    assert "Flip Override:" in MENU
    assert "Minimum estimated net profit" in MENU
    assert "compose_category_preferences(" in MENU
    assert "muted=self.muted_categories" in MENU


def test_dm_deals_keeps_typed_category_controls_as_backup() -> None:
    assert 'mute_categories="Hide categories from normal DMs' in SOURCE
    assert 'unmute_categories="Restore personally muted categories' in SOURCE
    assert "mute_categories: app_commands.Range[str, 1, 300] | None" in SOURCE
    assert "unmute_categories: app_commands.Range[str, 1, 300] | None" in SOURCE
    assert "update_muted_categories(" in SOURCE


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
    assert "flip_min_profit: app_commands.Range[float, 10.0, 100000.0] | None" in SOURCE
    assert "update_flip_settings(" in SOURCE
    assert 'FLIP_ALERTS_TOKEN = "flip:enabled"' in PERSONAL
    assert 'FLIP_MIN_PROFIT_PREFIX = "flip_profit:"' in PERSONAL


def test_category_mutes_and_favorites_are_personal_delivery_filters() -> None:
    assert 'MUTED_CATEGORY_PREFIX = "muted:"' in PERSONAL
    assert 'FAVORITE_CATEGORY_PREFIX = "favorite:"' in PERSONAL
    assert '"baby": ("baby_kids",)' in PERSONAL
    assert '"pc": ("gpus", "cpus", "ram", "ssds")' in PERSONAL
    assert '"category is muted in your personal DMs"' in MATCHER
    assert "category_key in muted_categories" in MATCHER
    assert "category_key in favorite_categories" in MATCHER
    assert "selected_categories and category_key not in selected_categories" in MATCHER
    assert "favorite-category priority" in MATCHER


def test_menu_category_storage_is_not_keyword_capped() -> None:
    assert "Category choices are intentionally uncapped" in PERSONAL
    assert "muted_tokens" in PERSONAL
    assert "normalize_terms(keywords)" in PERSONAL
    assert "compose_exclude_terms(self.exclude_keywords)" in MENU


def test_flip_lane_never_treats_active_listings_as_sold_proof() -> None:
    assert "ebayRecentSoldCount" in FLIP
    assert "ebayMedianSoldPrice" in FLIP
    assert "ebayCompIdentityMatched" in FLIP
    assert "ebayCompConditionMatched" in FLIP
    assert "ebayActiveListing" not in FLIP
    assert "recent sold comps not connected" in FLIP
    assert "assess_flip_opportunity(" in MATCHER
    assert "if flip.qualified" in MATCHER


def test_flip_lane_keeps_hard_personal_limits_before_category_override() -> None:
    max_price_position = MATCHER.index("if pref.max_price_cents is not None")
    required_keyword_position = MATCHER.index("if pref.keywords and not any")
    exclusion_position = MATCHER.index("if keyword_excludes and any")
    flip_position = MATCHER.index("if flip_enabled:")
    mute_position = MATCHER.index("if category_key in muted_categories")
    threshold_position = MATCHER.index("if discount < required_discount")

    assert max_price_position < flip_position
    assert required_keyword_position < flip_position
    assert exclusion_position < flip_position
    assert flip_position < mute_position < threshold_position
