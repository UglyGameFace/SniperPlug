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
