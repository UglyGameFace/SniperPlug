from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
SCANNER = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
ACTIVE = Path("sniperplug/cogs/active_deals.py").read_text(encoding="utf-8")


def test_public_guard_prepares_candidates_before_should_alert_gate():
    assert "prepare_public_deal_candidate" in POSTS
    assert "min_public_discount" in POSTS
    assert POSTS.index("prepare_public_deal_candidate") < POSTS.index("should_alert = getattr")


def test_autoscan_has_verified_markdown_rescue_lane():
    assert "select_public_deal_candidates" in AUTO
    assert "Verified markdown rescue lane added" in AUTO
    assert "shown_cards = watchlist_cards" not in AUTO
    assert "Public Scout Lane only posts high-confidence leads" in AUTO
    assert "allow_review_scout=True" not in AUTO
    assert "Public Scout Lane is disabled for public posts" in AUTO


def test_manual_commands_post_public_candidates_not_raw_cards():
    assert "public_cards = select_public_deal_candidates" in SCANNER
    assert "cards=public_cards" in SCANNER
    assert "min_public_discount=shown_discount" in SCANNER


def test_active_deals_hides_zero_percent_junk_by_default():
    assert "Public Deal Cache" in ACTIVE
    assert "public_quality_only" in ACTIVE
    assert "LOWER(source_label) NOT LIKE '%watchlist%'" in ACTIVE
