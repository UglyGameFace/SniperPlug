from pathlib import Path


SOURCE = Path("sniperplug/cogs/dm_deal_alerts.py").read_text(encoding="utf-8")
MENU = Path("sniperplug/cogs/dm_deal_preferences_view.py").read_text(encoding="utf-8")
MATCHER = Path("sniperplug/services/dm_deal_matching.py").read_text(encoding="utf-8")
PERSONAL = Path("sniperplug/services/dm_personal_categories.py").read_text(encoding="utf-8")


def test_dm_deals_exposes_complete_personalization_menu() -> None:
    assert 'Choice(name="Open personalization menu", value="menu")' in SOURCE
    assert "DmDealPreferencesView(" in SOURCE
    assert "all_personal_categories()" in MENU
    assert "personal_category_pages(" in MENU
    assert "Search every deal category" in MENU
    assert "All categories" in MENU
    assert "CATEGORY_PAGE_SIZE = 25" in PERSONAL


def test_menu_is_three_questions_with_plain_route_toggles() -> None:
    assert 'SCREEN_HOME = "home"' in MENU
    assert 'SCREEN_WHAT = "what"' in MENU
    assert 'SCREEN_PRODUCTS = "products"' in MENU
    assert 'SCREEN_CATEGORIES = "categories"' in MENU
    assert 'SCREEN_QUALITY = "quality"' in MENU
    assert 'SCREEN_COUNT = "count"' in MENU
    assert '"What DMs you"' in MENU
    assert '"How good it has to be"' in MENU
    assert '"How many a day"' in MENU
    assert "personal_route_labels()" in MENU
    assert "summary_sentence()" in MENU


def test_removed_features_are_gone_from_menu_and_command() -> None:
    for gone in (
        "MODE_CHOICES",
        "TAB_FAVORITES",
        "TAB_MUTED",
        "TAB_ALLOWLIST",
        "_DmDealRulesModal",
        "_FlipProfitModal",
        "DmDealRuleDraft",
        "parse_dm_rule_values",
        "favorite_categories",
        "muted_categories",
        "min_score",
        "min_savings",
        "keywords",
        "exclude",
        "walmart_cash_only",
        "Smart",
    ):
        assert gone not in MENU, gone
        assert gone not in SOURCE, gone


def test_matcher_is_a_union_without_vetoes_or_overrides() -> None:
    assert "matched_layers" in MATCHER
    assert "category is muted in your personal DMs" not in MATCHER
    assert "favorite-category priority" not in MATCHER
    assert "assess_flip_opportunity" not in MATCHER
    assert "priority_override" not in MATCHER
    assert "explicit_flip_min_profit_cents" in MATCHER
    assert "DM_INTERNAL_MIN_SCORE = 78" in MATCHER
    assert "split_category_preferences" in MATCHER


def test_flip_floor_storage_tokens_survive() -> None:
    assert 'FLIP_ALERTS_TOKEN = "flip:enabled"' in PERSONAL
    assert 'FLIP_MIN_PROFIT_PREFIX = "flip_profit:"' in PERSONAL
    assert '"baby": ("baby_kids",)' in PERSONAL
    assert '"pc": ("desktop_computers", "gpus", "cpus", "ram", "ssds")' in PERSONAL
    assert "def explicit_flip_min_profit_cents(" in PERSONAL


def test_flip_module_is_deleted() -> None:
    assert not Path("sniperplug/services/dm_flip_opportunities.py").exists()
