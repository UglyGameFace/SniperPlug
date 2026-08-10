from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY = (ROOT / "sniperplug/services/active_deal_history.py").read_text(encoding="utf-8")
MAINTENANCE = (ROOT / "sniperplug/services/storage_maintenance.py").read_text(encoding="utf-8")
CATALOG = (ROOT / "sniperplug/services/command_catalog.py").read_text(encoding="utf-8")
SURFACE = (ROOT / "sniperplug/services/command_surface.py").read_text(encoding="utf-8")
ACTIVE_DEALS = (ROOT / "sniperplug/cogs/active_deals.py").read_text(encoding="utf-8")


def test_global_history_prune_enforces_age_and_per_guild_cap():
    assert "DELETE FROM guild_active_deal_history WHERE occurred_at < ?" in HISTORY
    assert "newer.guild_id = history.guild_id" in HISTORY
    assert ") >= ?" in HISTORY
    assert "deleted += _row_count(cursor)" in HISTORY


def test_storage_maintenance_prunes_history_and_reports_it():
    assert "from sniperplug.services.active_deal_history import prune_active_deal_history" in MAINTENANCE
    assert "old_active_deal_history: int = 0" in MAINTENANCE
    assert "old_active_deal_history = await prune_active_deal_history(db)" in MAINTENANCE
    assert '"old_active_deal_history": self.old_active_deal_history' in MAINTENANCE
    assert "+ self.old_active_deal_history" in MAINTENANCE


def test_storage_maintenance_deletes_only_terminal_hot_deal_rows_in_bounded_batches():
    assert "OPERATIONAL_CLEANUP_BATCH_SIZE = 500" in MAINTENANCE
    assert "ACTIVE_DEAL_TERMINAL_RETENTION_DAYS = 7" in MAINTENANCE
    assert "status IN ('active', 'stale') AND last_seen_at < ?" in MAINTENANCE
    assert "status IN ('stale', 'expired') AND last_seen_at < ?" in MAINTENANCE
    assert "old_inactive_active_deals" in MAINTENANCE
    assert "async def delete_bounded_rows(" in MAINTENANCE
    assert "SELECT rowid FROM {table}" in MAINTENANCE
    assert "if not rowids:" in MAINTENANCE


def test_storage_maintenance_keeps_price_history_and_prunes_only_expired_recreatable_caches():
    assert "DELETE FROM price_observations" not in MAINTENANCE
    assert "walmart_offer_price_memory" not in MAINTENANCE
    assert 'table="provider_response_cache"' in MAINTENANCE
    assert 'table="scan_result_cache"' in MAINTENANCE
    assert 'table="alert_dedupe"' in MAINTENANCE
    assert 'table="store_cache"' in MAINTENANCE
    assert 'table="scan_runs"' in MAINTENANCE
    assert "expires_at <= ?" in MAINTENANCE


def test_owner_catalog_uses_active_deals_controls_and_keeps_history():
    assert 'name="/active_deals"' in CATALOG
    assert 'name="/active_deal_history"' in CATALOG
    assert 'audience="Owner"' in CATALOG
    assert "single-item rechecks" in CATALOG
    assert "historical evidence" in CATALOG

    assert '"active_deals_recheck"' in SURFACE
    assert '"active_deal_recheck"' in SURFACE
    assert '"active_deals_cleanup"' in SURFACE
    assert "Recheck Walmart on Page" in ACTIVE_DEALS
