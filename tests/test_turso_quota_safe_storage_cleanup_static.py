from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAINTENANCE = (ROOT / "sniperplug/services/storage_maintenance.py").read_text(encoding="utf-8")
STORAGE_ADMIN = (ROOT / "sniperplug/cogs/storage_admin.py").read_text(encoding="utf-8")
FEEDBACK_VIEWS = (ROOT / "sniperplug/services/bounded_feedback_views.py").read_text(encoding="utf-8")


def test_cleanup_retention_targets_disposable_operational_state():
    assert "SCAN_RUN_RETENTION_DAYS = 7" in MAINTENANCE
    assert "AUTOSCAN_REPORT_RETENTION_DAYS = 7" in MAINTENANCE
    assert "FEEDBACK_EVENT_RETENTION_DAYS = 30" in MAINTENANCE
    assert "PUBLIC_POST_RETENTION_DAYS = 31" in MAINTENANCE
    assert "ERROR_EVENT_RETENTION_DAYS = 30" in MAINTENANCE
    assert "RETAILER_AUTOSCAN_RUN_RETENTION_DAYS = 8" in MAINTENANCE
    assert "DM_RECEIPT_RETENTION_DAYS = 90" in MAINTENANCE
    assert "FEEDBACK_TARGET_MAX_ROWS = 500" in MAINTENANCE


def test_cleanup_installs_selective_timestamp_indexes():
    for token in (
        "idx_feedback_targets_created_cleanup",
        "idx_feedback_events_created_cleanup",
        "idx_active_deal_cache_status_seen_cleanup",
        "idx_auto_scan_report_ran_cleanup",
        "idx_public_posts_status_first_cleanup",
        "idx_public_posts_status_posted_cleanup",
        "idx_store_cache_expires_cleanup",
        "idx_scan_runs_started_cleanup",
        "idx_retailer_auto_scan_runs_ran_cleanup",
        "idx_dm_deal_alert_receipts_delivered_cleanup",
    ):
        assert token in MAINTENANCE


def test_cleanup_uses_bounded_ordered_rowid_batches_not_table_counts():
    assert "async def _bounded_rowids(" in MAINTENANCE
    assert "ORDER BY {order_by_sql} LIMIT ?" in MAINTENANCE
    assert "async def delete_oldest_rows_beyond_limit(" in MAINTENANCE
    assert "LIMIT 1 OFFSET ?" in MAINTENANCE
    assert "COUNT(*)" not in MAINTENANCE


def test_cleanup_preserves_price_and_exact_offer_intelligence():
    assert "DELETE FROM price_observations" not in MAINTENANCE
    assert "walmart_offer_price_memory" not in MAINTENANCE
    assert "walmart_exact_detail_queue" not in MAINTENANCE
    assert "Product identity" in MAINTENANCE


def test_manual_maintenance_does_not_run_before_after_count_sweeps():
    command_start = STORAGE_ADMIN.index("async def storage_maintenance_now(")
    command_end = STORAGE_ADMIN.index("@storage_maintenance_now.error", command_start)
    command_body = STORAGE_ADMIN[command_start:command_end]
    assert "await run_storage_maintenance" in command_body
    assert "storage_counts(" not in command_body
    assert "before =" not in command_body
    assert "after =" not in command_body
    assert "skips before/after full row counts" in command_body


def test_persistent_feedback_restore_reads_only_capacity():
    assert "remaining = max(0, MAX_PERSISTENT_FEEDBACK_VIEWS - registered)" in FEEDBACK_VIEWS
    assert "recent_feedback_targets(db, limit=remaining)" in FEEDBACK_VIEWS
    assert "recent_feedback_targets(db)" not in FEEDBACK_VIEWS
    assert "skipped_old" not in FEEDBACK_VIEWS
