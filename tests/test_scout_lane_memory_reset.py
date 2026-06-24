from pathlib import Path


POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")
PUBLIC_ALERTS = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_scout_lane_has_short_dedupe_window():
    assert "SCOUT_ALERT_DEDUPE_HOURS = 6" in POSTS
    assert "PUBLIC_SCOUT_ALERT_KEY" in POSTS
    assert "hours=SCOUT_ALERT_DEDUPE_HOURS if allow_review_scout else None" in POSTS


def test_recent_alert_dedupe_is_alert_key_scoped():
    assert "alert_key: str | None = None" in DB
    assert "AND alert_key = ?" in DB
    assert "alert_key=alert_key" in POSTS


def test_scout_public_post_reservations_expire():
    assert "str(deal_key).startswith(\"scout:\")" in POSTS
    assert "SCOUT_ALERT_DEDUPE_HOURS" in POSTS
    assert "COALESCE(posted_at, first_seen_at)" in POSTS


def test_autoscan_clear_cache_clears_actual_post_blockers():
    assert "clear_autoscan_posting_memory" in PUBLIC_ALERTS
    assert "DELETE FROM guild_public_deal_posts WHERE guild_id = ?" in PUBLIC_ALERTS
    assert "DELETE FROM alert_dedupe WHERE guild_id = ? AND retailer = 'walmart'" in PUBLIC_ALERTS


def test_ghost_rows_are_deleted_not_warned_forever():
    assert "delete_ghost_public_alert_guild_row" in AUTO
    assert "Auto-scan deleted stale/ghost public-alert guild row" in AUTO
    assert "\"guild_public_alert_settings\"" in AUTO
    assert "for table in tables" in AUTO
    assert 'f"DELETE FROM {table} WHERE guild_id = ?"' in AUTO
