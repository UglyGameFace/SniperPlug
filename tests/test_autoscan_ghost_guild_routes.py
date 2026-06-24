from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
HEALTH = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")


def test_autoscan_loop_passes_bot_to_guild_loader():
    assert "list_public_alert_guilds(self.bot.db, bot=self.bot)" in AUTO


def test_list_public_alert_guilds_skips_ghost_rows():
    assert "live_guild_ids" in AUTO
    assert "Auto-scan skipped stale/ghost public-alert guild row" in AUTO
    assert "guild_id not in live_guild_ids" in AUTO


def test_public_post_resolver_explains_ghost_guild_route():
    assert "ghost guild" in POSTS
    assert "saved channel <#{channel_id}> belongs to live guild" in POSTS
    assert "Run `/setup_sniperplug_here`" in POSTS


def test_health_flags_route_error_as_not_healthy():
    assert "last_run_has_route_error" in HEALTH
    assert "Posting route problem" in HEALTH
    assert "not last_run_has_route_error" in HEALTH
