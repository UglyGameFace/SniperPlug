from pathlib import Path


SELF_HEAL = Path("sniperplug/services/setup_self_heal.py").read_text(encoding="utf-8")
BOT = Path("sniperplug/bot.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
PUBLIC = Path("sniperplug/cogs/canonical_public_alerts.py").read_text(encoding="utf-8")
SETUP = Path("sniperplug/cogs/canonical_workflow.py").read_text(encoding="utf-8")
DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
VERIFIED = Path("sniperplug/cogs/verified_deal_scanner.py").read_text(encoding="utf-8")
CASH = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
PIPELINE = Path("sniperplug/services/walmart_cash_pipeline.py").read_text(encoding="utf-8")
API_TRUTH = Path("sniperplug/services/walmart_cash_api_truth.py").read_text(encoding="utf-8")
CATALOG = Path("sniperplug/services/command_catalog.py").read_text(encoding="utf-8")


def test_setup_repair_service_exists_and_cleans_ghost_rows():
    assert "repair_public_alert_setup" in SELF_HEAL
    assert "repair_all_public_alert_setups" in SELF_HEAL
    assert "cleanup_ghost_setup_rows" in SELF_HEAL
    assert "guild_public_alert_settings" in SELF_HEAL
    assert "guild_retailer_auto_scan_settings" in SELF_HEAL


def test_setup_repair_is_not_installed_as_a_second_startup_command_flow():
    assert "repair_all_public_alert_setups" not in BOT
    assert "Setup self-heal complete" not in BOT
    assert "_setup_self_heal_done" not in BOT
    assert "from sniperplug.services.setup_self_heal" not in BOT


def test_autoscan_can_repair_explicit_setup_without_startup_guard():
    assert "repair_public_alert_setup" in AUTO
    assert "Public alerts are still missing after self-heal" in AUTO
    assert "This should only require setup on first install" in AUTO


def test_canonical_autoscan_health_shows_self_heal_and_global_fanout():
    assert "repair_public_alert_setup" in PUBLIC
    assert "Setup repair" in PUBLIC
    assert "Global catalog coverage" in PUBLIC
    assert "Live fanout enrollment" in PUBLIC
    assert "does not need rerun" in SELF_HEAL


def test_walmart_cash_only_command_and_button_exist():
    assert 'name="walmart_cash"' in DEALS
    assert "WalmartCashOffersButton" in DEALS
    assert "WalmartCashOffersButton(row=4)" in VERIFIED
    assert "find_walmart_cash_offer" in DEALS
    assert "build_walmart_cash_summary_embed" in DEALS


def test_walmart_cash_only_blocks_guesses_onepay_and_query_text():
    assert "OnePay cashback" in CASH
    assert "generic promo text" in CASH or "generic rewards" in CASH
    assert "_reject_badge_path" in PIPELINE
    assert '"query"' in PIPELINE
    assert '"title"' in PIPELINE
    assert "does not public-post markdown alerts" in CASH.lower()

    assert "BLOCKED_NON_WALMART_CASH_TERMS" in API_TRUTH
    assert "onepay" in API_TRUTH
    assert "one pay" in API_TRUTH
    assert "cashrewards" in API_TRUTH
    assert "extract_walmart_cash_api_truth" in API_TRUTH
    assert "return None" in API_TRUTH


def test_command_catalog_lists_cash_and_one_setup_command():
    assert 'name="/walmart_cash"' in CATALOG
    assert "Run once during installation" in CATALOG
    assert CATALOG.count('name="/setup_sniperplug_here"') == 1
    assert SETUP.count('name="setup_sniperplug_here"') == 1
    assert "global" in SETUP.lower()
