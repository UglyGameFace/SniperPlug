from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
RESILIENT = Path("sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")
RECONCILIATION = Path(
    "sniperplug/services/autoscan_live_guild_reconciliation.py"
).read_text(encoding="utf-8")
POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
HEALTH = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")


def test_resilient_autoscan_uses_single_owner_live_guild_loader():
    assert "reconcile_live_public_alert_setups(self.bot.db, self.bot)" in RESILIENT
    assert "list_live_public_alert_guilds(self.bot.db, self.bot)" in RESILIENT
    assert "legacy.list_public_alert_guilds" not in RESILIENT


def test_live_guild_loader_filters_without_second_delete_pass():
    assert "This function never deletes" in RECONCILIATION
    assert "guild_id not in live_guild_ids" in RECONCILIATION
    assert "delete_ghost_public_alert_guild_row" not in RECONCILIATION
    assert "tombstoned_visible_ids" in RECONCILIATION


def test_scheduled_runner_guards_live_guild_before_scan():
    assert RESILIENT.count("is_live_bot_guild(self.bot, gid)") >= 3
    assert "Dropped stale scheduled autoscan before start" in RESILIENT
    assert "Dropped stale scheduled autoscan after lock wait" in RESILIENT


def test_legacy_loader_still_fails_closed_for_direct_callers():
    assert "live_guild_ids" in AUTO
    assert "guild_id not in live_guild_ids" in AUTO


def test_public_post_resolver_explains_ghost_guild_route():
    assert "ghost guild" in POSTS
    assert "saved channel <#{channel_id}> belongs to live guild" in POSTS
    assert "Run `/setup_sniperplug_here`" in POSTS


def test_health_flags_route_error_as_not_healthy():
    assert "last_run_has_route_error" in HEALTH
    assert "Posting route problem" in HEALTH
    assert "not last_run_has_route_error" in HEALTH
