from pathlib import Path


SELF_HEAL = Path("sniperplug/services/setup_self_heal.py").read_text(encoding="utf-8")
BOT = Path("sniperplug/bot.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
PUBLIC = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
VERIFIED = Path("sniperplug/cogs/verified_deal_scanner.py").read_text(encoding="utf-8")
CASH = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
CATALOG = Path("sniperplug/services/command_catalog.py").read_text(encoding="utf-8")


def test_setup_self_heal_exists_and_cleans_ghost_rows():
    assert "repair_public_alert_setup" in SELF_HEAL
    assert "repair_all_public_alert_setups" in SELF_HEAL
    assert "cleanup_ghost_setup_rows" in SELF_HEAL
    assert "guild_public_alert_settings" in SELF_HEAL
    assert "guild_retailer_auto_scan_settings" in SELF_HEAL


def test_setup_self_heal_runs_on_ready_and_autoscan_now():
    assert "repair_all_public_alert_setups" in BOT
    assert "Setup self-heal complete" in BOT
    assert "repair_public_alert_setup" in AUTO
    assert "Public alerts are still missing after self-heal" in AUTO
    assert "This should only require setup on first install" in AUTO


def test_autoscan_health_shows_self_heal_not_repeat_setup_forever():
    assert "repair_public_alert_setup" in PUBLIC
    assert "Self-heal" in PUBLIC
    assert "does not need rerun" in SELF_HEAL
    assert "Run `/setup_sniperplug_here` inside the live #walmart-deals channel" not in PUBLIC


def test_walmart_cash_only_command_and_button_exist():
    assert 'name="walmart_cash"' in DEALS
    assert "WalmartCashOffersButton" in DEALS
    assert "WalmartCashOffersButton(row=4)" in VERIFIED
    assert "find_walmart_cash_offer" in DEALS
    assert "build_walmart_cash_summary_embed" in DEALS


def test_walmart_cash_only_blocks_guesses_and_onepay():
    assert "walmartCashSavings" in CASH
    assert "onepay" in CASH
    assert "generic rewards" in CASH
    assert "search words" in CASH
    assert "if \"onepay\" in joined.lower()" in CASH
    assert "return None" in CASH
    assert "does not public-post markdown alerts" in CASH


def test_command_catalog_lists_cash_and_setup_once():
    assert 'name="/walmart_cash"' in CATALOG
    assert "Run once during first install" in CATALOG
    assert CATALOG.count('name="/setup_sniperplug_here"') == 1
