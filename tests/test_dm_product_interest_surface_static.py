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


def test_dm_deals_exposes_specific_product_and_brand_controls() -> None:
    assert 'Choice(name="Show specific product catalog", value="interests")' in COG
    assert 'product_interests="Add specific products:' in COG
    assert 'remove_interests="Remove specific product interests' in COG
    assert 'brands="Add brand filters' in COG
    assert 'remove_brands="Remove brands' in COG
    assert "update_product_preferences(" in COG
    assert "build_product_interest_catalog_embed(" in COG


def test_personalization_menu_exposes_products_without_overloading_category_tabs() -> None:
    assert 'label=f"Specific products: {len(self.product_interests)}"' in MENU
    assert 'label="Product catalog"' in MENU
    assert "class _ProductInterestModal(" in MENU
    assert "replace_interests=self.product_interests" in MENU
    assert "replace_brands=self.product_brands" in MENU
    assert "Products are OR; brands are OR" in MENU


def test_product_control_tokens_are_not_misread_as_category_allowlist_entries() -> None:
    assert "value.startswith(PRODUCT_INTEREST_PREFIX)" in PERSONAL
    assert "value.startswith(BRAND_PREFIX)" in PERSONAL
    assert "product_tokens=existing_categories" in PERSONAL


def test_all_live_fanouts_import_the_canonical_dm_matcher() -> None:
    canonical_import = "from sniperplug.services.dm_deal_matching import match_dm_deal"
    for source in (WALMART_BULK, WALMART_LEGACY, VERIFIED_RETAILER):
        assert canonical_import in source
        assert "from sniperplug.services.dm_deal_alerts import match_dm_deal" not in source


def test_authoritative_matcher_keeps_specific_intent_hard_even_for_flip() -> None:
    product_position = MATCHER.index("product_match = match_product_preferences(")
    flip_position = MATCHER.index("if flip_enabled:")
    assert product_position < flip_position
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
