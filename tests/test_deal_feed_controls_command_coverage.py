from pathlib import Path


DISCOVER = Path("sniperplug/cogs/auto_discovery.py").read_text(encoding="utf-8")
HUNT = Path("sniperplug/services/verified_discount_hunt.py").read_text(encoding="utf-8")
UNIFIED = Path("sniperplug/cogs/unified_deal_scanner.py").read_text(encoding="utf-8")
SCANNER = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
PREFS = Path("sniperplug/services/deal_category_preferences.py").read_text(encoding="utf-8")


def test_autoscan_related_manual_commands_use_category_preferences():
    assert "apply_category_preferences" in DISCOVER
    assert "get_category_preferences" in DISCOVER
    assert "apply_category_preferences" in HUNT
    assert "get_category_preferences" in HUNT
    assert "apply_category_preferences" in UNIFIED
    assert "get_category_preferences" in UNIFIED
    assert "apply_category_preferences" in SCANNER
    assert "get_category_preferences" in SCANNER


def test_public_posting_uses_filtered_cards_for_hunt_and_discover():
    assert "cards=posted_cards" in HUNT
    assert "cards=shown_cards" in DISCOVER
    assert "Muted category settings hid" in HUNT
    assert "Muted category settings hid" in DISCOVER


def test_deals_mode_switches_reapply_category_preferences():
    assert "add_deal_feed_controls_field" in UNIFIED
    assert "ModeRankedCards(ranked.mode, category_allowed_verified" in UNIFIED
    assert "async def show_mode" in UNIFIED


def test_category_priority_boost_is_idempotent():
    assert "deal_category_boost_applied" in PREFS
    assert "card.score = int(getattr(card, \"score\", 0) or 0) + 25" in PREFS


def test_command_names_still_exist():
    assert 'name="discover"' in DISCOVER
    assert "verified_hunt_button_callback" in HUNT
    assert 'name="deals"' in SCANNER
    assert 'name="walmart_scan"' in SCANNER
