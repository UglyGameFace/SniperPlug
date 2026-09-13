from pathlib import Path


COG = Path("sniperplug/cogs/dm_deal_alerts.py").read_text(encoding="utf-8")
MENU = Path("sniperplug/cogs/dm_deal_preferences_view.py").read_text(encoding="utf-8")
MATCHER = Path("sniperplug/services/dm_deal_matching.py").read_text(encoding="utf-8")
PERSONAL = Path("sniperplug/services/dm_personal_categories.py").read_text(encoding="utf-8")
PRODUCTS = Path("sniperplug/services/dm_product_interests.py").read_text(encoding="utf-8")
BADGES = Path("sniperplug/services/dm_deal_badges.py").read_text(encoding="utf-8")
WALMART_BULK = Path("sniperplug/services/walmart_global_deal_fanout_bulk.py").read_text(encoding="utf-8")
WALMART_LEGACY = Path("sniperplug/services/walmart_global_deal_fanout.py").read_text(encoding="utf-8")
VERIFIED_RETAILER = Path("sniperplug/services/verified_retailer_event_fanout.py").read_text(encoding="utf-8")


def test_dm_deals_keeps_typed_product_and_brand_controls_as_backup() -> None:
    assert 'Choice(name="Show specific product catalog", value="interests")' in COG
    assert 'product_interests="Add specific products:' in COG
    assert 'remove_interests="Remove specific product interests' in COG
    assert 'brands="Add brand filters' in COG
    assert 'remove_brands="Remove brands' in COG
    assert "update_product_preferences(" in COG


def test_personalization_menu_uses_guided_product_select_instead_of_product_text_guessing() -> None:
    assert 'SCREEN_HOME = "home"' in MENU
    assert 'SCREEN_PRODUCTS = "products"' in MENU
    assert 'SCREEN_CATEGORIES = "categories"' in MENU
    assert "class _ProductPageSelect(" in MENU
    assert 'placeholder="Select the specific products you want"' in MENU
    assert 'label="Dashboard"' in MENU
    assert 'label="Product catalog"' not in MENU
    assert "class _ProductInterestModal(" not in MENU
    assert "replace_interests=self.product_interests" in MENU
    assert "replace_brands=self.product_brands" in MENU


def test_product_catalog_supports_obvious_human_aliases_and_safe_pages() -> None:
    assert '"iphones"' in PRODUCTS
    assert '"airpods"' in PRODUCTS
    assert '"tools"' in PRODUCTS
    assert "PRODUCT_PAGE_SIZE = 25" in PRODUCTS
    assert "def product_interest_pages(" in PRODUCTS
    assert "MAX_PRODUCT_INTERESTS = 50" in PRODUCTS
    assert "MAX_BRANDS = 50" in PRODUCTS


def test_product_control_tokens_are_not_misread_as_category_allowlist_entries() -> None:
    assert "value.startswith(PRODUCT_INTEREST_PREFIX)" in PERSONAL
    assert "value.startswith(BRAND_PREFIX)" in PERSONAL
    assert "product_tokens=existing_categories" in PERSONAL


def test_all_live_fanouts_import_the_canonical_dm_matcher() -> None:
    canonical_import = "from sniperplug.services.dm_deal_matching import match_dm_deal"
    for source in (WALMART_BULK, WALMART_LEGACY, VERIFIED_RETAILER):
        assert canonical_import in source
        assert "from sniperplug.services.dm_deal_alerts import match_dm_deal" not in source


def test_authoritative_matcher_keeps_specific_intent_hard_and_separate_from_categories() -> None:
    product_position = MATCHER.index("product_match = match_product_preferences(")
    flip_position = MATCHER.index("if flip_enabled:")
    assert product_position < flip_position
    assert "has_specific_walmart_intent" in MATCHER
    assert "not has_specific_walmart_intent" in MATCHER
    assert "explicit product intent" in MATCHER
    assert "none of your specific product interests matched" in PRODUCTS
    assert "none of your selected brands matched" in PRODUCTS


def test_specific_filters_and_badges_are_walmart_only() -> None:
    assert 'is_walmart = retailer.startswith("walmart")' in MATCHER
    assert "if is_walmart:" in MATCHER
    assert "HP/Target delivery retains its existing behavior" in MATCHER


def test_dm_badges_are_product_aware_and_bounded_to_two() -> None:
    assert "badges[:2]" in BADGES
    assert 'return "💸"' in BADGES
    assert "product_emoji_for_text(product_text)" in BADGES
    assert "personalize_dm_card_title(card)" in MATCHER
