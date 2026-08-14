from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.active_deal_history import prune_active_deal_history
from sniperplug.services.autoscan_history import ensure_autoscan_history_table
from sniperplug.services.deal_feedback import ensure_deal_feedback_tables
from sniperplug.services.error_logging import ensure_error_logging_table
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.walmart_recheck_audit import prune_walmart_recheck_audit


OPERATIONAL_CLEANUP_BATCH_SIZE = 500
ACTIVE_DEAL_TERMINAL_RETENTION_DAYS = 7
SCAN_RUN_RETENTION_DAYS = 7
AUTOSCAN_REPORT_RETENTION_DAYS = 7
FEEDBACK_EVENT_RETENTION_DAYS = 30
PUBLIC_POST_RETENTION_DAYS = 31
ERROR_EVENT_RETENTION_DAYS = 30
RETAILER_AUTOSCAN_RUN_RETENTION_DAYS = 8
DM_RECEIPT_RETENTION_DAYS = 90
FEEDBACK_TARGET_MAX_ROWS = 500
_MAINTENANCE_ROWID_ALIAS = "__sniperplug_rowid"
_CLEANUP_INDEX_CONNECTIONS: set[tuple[int, str]] = set()


@dataclass(frozen=True)
class MaintenanceCleanupResult:
    expired_feedback_targets: int = 0
    excess_feedback_targets: int = 0
    old_feedback_events: int = 0
    stale_active_deals_marked_expired: int = 0
    old_inactive_active_deals: int = 0
    old_autoscan_reports: int = 0
    old_public_post_reservations: int = 0
    old_public_posts: int = 0
    old_public_sending_rows: int = 0
    expired_provider_cache: int = 0
    expired_scan_result_cache: int = 0
    expired_alert_dedupe: int = 0
    expired_store_cache: int = 0
    old_scan_runs: int = 0
    old_error_events: int = 0
    old_retailer_autoscan_runs: int = 0
    old_dm_receipts: int = 0
    old_active_deal_history: int = 0
    old_walmart_recheck_audit: int = 0

    @property
    def total_changed(self) -> int:
        return sum(self.log_fields(include_total=False).values())

    def log_fields(self, *, include_total: bool = True) -> dict[str, int]:
        fields = {
            "expired_feedback_targets": self.expired_feedback_targets,
            "excess_feedback_targets": self.excess_feedback_targets,
            "old_feedback_events": self.old_feedback_events,
            "stale_active_deals_marked_expired": self.stale_active_deals_marked_expired,
            "old_inactive_active_deals": self.old_inactive_active_deals,
            "old_autoscan_reports": self.old_autoscan_reports,
            "old_public_post_reservations": self.old_public_post_reservations,
            "old_public_posts": self.old_public_posts,
            "old_public_sending_rows": self.old_public_sending_rows,
            "expired_provider_cache": self.expired_provider_cache,
            "expired_scan_result_cache": self.expired_scan_result_cache,
            "expired_alert_dedupe": self.expired_alert_dedupe,
            "expired_store_cache": self.expired_store_cache,
            "old_scan_runs": self.old_scan_runs,
            "old_error_events": self.old_error_events,
            "old_retailer_autoscan_runs": self.old_retailer_autoscan_runs,
            "old_dm_receipts": self.old_dm_receipts,
            "old_active_deal_history": self.old_active_deal_history,
            "old_walmart_recheck_audit": self.old_walmart_recheck_audit,
        }
        if include_total:
            fields["total_changed"] = sum(fields.values())
        return fields


async def run_storage_maintenance(
    db,
    *,
    feedback_event_days: int = FEEDBACK_EVENT_RETENTION_DAYS,
    active_deal_stale_hours: int = 48,
    active_deal_terminal_days: int = ACTIVE_DEAL_TERMINAL_RETENTION_DAYS,
    autoscan_report_days: int = AUTOSCAN_REPORT_RETENTION_DAYS,
    public_reservation_hours: int = 6,
    public_post_days: int = PUBLIC_POST_RETENTION_DAYS,
    scan_run_days: int = SCAN_RUN_RETENTION_DAYS,
    error_event_days: int = ERROR_EVENT_RETENTION_DAYS,
    retailer_autoscan_run_days: int = RETAILER_AUTOSCAN_RUN_RETENTION_DAYS,
    dm_receipt_days: int = DM_RECEIPT_RETENTION_DAYS,
    feedback_target_max_rows: int = FEEDBACK_TARGET_MAX_ROWS,
    cleanup_batch_size: int = OPERATIONAL_CLEANUP_BATCH_SIZE,
) -> MaintenanceCleanupResult:
    """Prune disposable operational state with bounded, index-seeking reads.

    This maintenance path is deliberately conservative about durable product
    intelligence. Product identity, ``price_observations``, exact Walmart queue
    state, exact Walmart offer-price memory, feedback summaries/votes, and live
    dedupe rows are never deleted here.

    Every potentially large cleanup first selects a small batch through the
    same timestamp/expiry column used by its retention predicate, then deletes
    only those rowids. This avoids trading a storage cleanup for another broad
    Turso rows-read spike.
    """
    await ensure_deal_feedback_tables(db)
    await ensure_public_post_tables(db)
    await ensure_autoscan_history_table(db)
    await ensure_error_logging_table(db)
    conn = db.require_conn()
    await ensure_quota_safe_cleanup_indexes(conn)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    batch_size = max(1, min(int(cleanup_batch_size), 5_000))

    expired_feedback_targets = await delete_bounded_rows(
        conn,
        table="guild_deal_feedback_targets",
        where_sql="expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
        order_by_sql="expires_at ASC, rowid ASC",
    )
    excess_feedback_targets = await delete_oldest_rows_beyond_limit(
        conn,
        table="guild_deal_feedback_targets",
        timestamp_column="created_at",
        keep=max(1, int(feedback_target_max_rows)),
        limit=batch_size,
    )
    old_feedback_events = await delete_bounded_rows(
        conn,
        table="guild_deal_feedback_events",
        where_sql="created_at < ?",
        params=((now - timedelta(days=max(1, int(feedback_event_days)))).isoformat(),),
        limit=batch_size,
        order_by_sql="created_at ASC, rowid ASC",
    )
    stale_active_deals_marked_expired = await update_bounded_rows(
        conn,
        table="guild_active_deal_cache",
        set_sql="status = 'expired'",
        where_sql="status IN ('active', 'stale') AND last_seen_at < ?",
        params=((now - timedelta(hours=max(1, int(active_deal_stale_hours)))).isoformat(),),
        limit=batch_size,
        order_by_sql="last_seen_at ASC, rowid ASC",
    )
    old_inactive_active_deals = await delete_bounded_rows(
        conn,
        table="guild_active_deal_cache",
        where_sql="status IN ('stale', 'expired', 'cleared') AND last_seen_at < ?",
        params=((now - timedelta(days=max(1, int(active_deal_terminal_days)))).isoformat(),),
        limit=batch_size,
        order_by_sql="last_seen_at ASC, rowid ASC",
    )
    old_autoscan_reports = await delete_bounded_rows(
        conn,
        table="guild_auto_scan_report_history",
        where_sql="ran_at < ?",
        params=((now - timedelta(days=max(1, int(autoscan_report_days)))).isoformat(),),
        limit=batch_size,
        order_by_sql="ran_at ASC, rowid ASC",
    )
    old_public_post_reservations = await delete_bounded_rows(
        conn,
        table="guild_public_deal_posts",
        where_sql="status = 'reserved' AND first_seen_at < ?",
        params=((now - timedelta(hours=max(1, int(public_reservation_hours)))).isoformat(),),
        limit=batch_size,
        order_by_sql="first_seen_at ASC, rowid ASC",
    )
    public_post_cutoff = (now - timedelta(days=max(1, int(public_post_days)))).isoformat()
    old_public_posts = await delete_bounded_rows(
        conn,
        table="guild_public_deal_posts",
        where_sql="status = 'posted' AND posted_at IS NOT NULL AND posted_at < ?",
        params=(public_post_cutoff,),
        limit=batch_size,
        order_by_sql="posted_at ASC, rowid ASC",
    )
    old_public_sending_rows = await delete_bounded_rows(
        conn,
        table="guild_public_deal_posts",
        where_sql="status = 'sending' AND first_seen_at < ?",
        params=(public_post_cutoff,),
        limit=batch_size,
        order_by_sql="first_seen_at ASC, rowid ASC",
    )

    # Re-creatable cache/dedupe state is safe to remove only after its own
    # expiry timestamp. Live duplicate protection is intentionally preserved.
    expired_provider_cache = await delete_bounded_rows(
        conn,
        table="provider_response_cache",
        where_sql="expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
        order_by_sql="expires_at ASC, rowid ASC",
    )
    expired_scan_result_cache = await delete_bounded_rows(
        conn,
        table="scan_result_cache",
        where_sql="expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
        order_by_sql="expires_at ASC, rowid ASC",
    )
    expired_alert_dedupe = await delete_bounded_rows(
        conn,
        table="alert_dedupe",
        where_sql="expires_at IS NOT NULL AND expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
        order_by_sql="expires_at ASC, rowid ASC",
    )
    expired_store_cache = await delete_bounded_rows(
        conn,
        table="store_cache",
        where_sql="expires_at IS NOT NULL AND expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
        order_by_sql="expires_at ASC, rowid ASC",
    )
    old_scan_runs = await delete_bounded_rows(
        conn,
        table="scan_runs",
        where_sql="started_at < ?",
        params=((now - timedelta(days=max(1, int(scan_run_days)))).isoformat(),),
        limit=batch_size,
        order_by_sql="started_at ASC, rowid ASC",
    )
    old_error_events = await delete_bounded_rows(
        conn,
        table="error_events",
        where_sql="created_at < ?",
        params=((now - timedelta(days=max(1, int(error_event_days)))).isoformat(),),
        limit=batch_size,
        order_by_sql="created_at ASC, rowid ASC",
    )

    old_retailer_autoscan_runs = 0
    if await table_exists(conn, "guild_retailer_auto_scan_runs"):
        old_retailer_autoscan_runs = await delete_bounded_rows(
            conn,
            table="guild_retailer_auto_scan_runs",
            where_sql="ran_at < ?",
            params=((now - timedelta(days=max(1, int(retailer_autoscan_run_days)))).isoformat(),),
            limit=batch_size,
            order_by_sql="ran_at ASC, rowid ASC",
        )

    old_dm_receipts = 0
    if await table_exists(conn, "user_dm_deal_alert_receipts"):
        old_dm_receipts = await delete_bounded_rows(
            conn,
            table="user_dm_deal_alert_receipts",
            where_sql="delivered_at < ?",
            params=((now - timedelta(days=max(1, int(dm_receipt_days)))).isoformat(),),
            limit=batch_size,
            order_by_sql="delivered_at ASC, rowid ASC",
        )

    operational_changed = sum(
        (
            expired_feedback_targets,
            excess_feedback_targets,
            old_feedback_events,
            stale_active_deals_marked_expired,
            old_inactive_active_deals,
            old_autoscan_reports,
            old_public_post_reservations,
            old_public_posts,
            old_public_sending_rows,
            expired_provider_cache,
            expired_scan_result_cache,
            expired_alert_dedupe,
            expired_store_cache,
            old_scan_runs,
            old_error_events,
            old_retailer_autoscan_runs,
            old_dm_receipts,
        )
    )
    if operational_changed:
        await conn.commit()

    # These helpers are separately bounded so startup maintenance never runs the
    # former global correlated COUNT-per-row cap queries.
    old_active_deal_history = await prune_active_deal_history(db)
    old_walmart_recheck_audit = await prune_walmart_recheck_audit(db)
    return MaintenanceCleanupResult(
        expired_feedback_targets=expired_feedback_targets,
        excess_feedback_targets=excess_feedback_targets,
        old_feedback_events=old_feedback_events,
        stale_active_deals_marked_expired=stale_active_deals_marked_expired,
        old_inactive_active_deals=old_inactive_active_deals,
        old_autoscan_reports=old_autoscan_reports,
        old_public_post_reservations=old_public_post_reservations,
        old_public_posts=old_public_posts,
        old_public_sending_rows=old_public_sending_rows,
        expired_provider_cache=expired_provider_cache,
        expired_scan_result_cache=expired_scan_result_cache,
        expired_alert_dedupe=expired_alert_dedupe,
        expired_store_cache=expired_store_cache,
        old_scan_runs=old_scan_runs,
        old_error_events=old_error_events,
        old_retailer_autoscan_runs=old_retailer_autoscan_runs,
        old_dm_receipts=old_dm_receipts,
        old_active_deal_history=old_active_deal_history,
        old_walmart_recheck_audit=old_walmart_recheck_audit,
    )


async def ensure_quota_safe_cleanup_indexes(conn: Any) -> None:
    """Install cleanup indexes once each, including tables created after startup."""
    connection_id = id(conn)
    changed = False

    base_key = (connection_id, "base")
    if base_key not in _CLEANUP_INDEX_CONNECTIONS:
        statements = (
            "CREATE INDEX IF NOT EXISTS idx_feedback_targets_created_cleanup ON guild_deal_feedback_targets (created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_events_created_cleanup ON guild_deal_feedback_events (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_active_deal_cache_status_seen_cleanup ON guild_active_deal_cache (status, last_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_auto_scan_report_ran_cleanup ON guild_auto_scan_report_history (ran_at)",
            "CREATE INDEX IF NOT EXISTS idx_public_posts_status_first_cleanup ON guild_public_deal_posts (status, first_seen_at)",
            "CREATE INDEX IF NOT EXISTS idx_public_posts_status_posted_cleanup ON guild_public_deal_posts (status, posted_at)",
            "CREATE INDEX IF NOT EXISTS idx_store_cache_expires_cleanup ON store_cache (expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_scan_runs_started_cleanup ON scan_runs (started_at)",
        )
        for statement in statements:
            await conn.execute(statement)
        _CLEANUP_INDEX_CONNECTIONS.add(base_key)
        changed = True

    retailer_key = (connection_id, "retailer_autoscan")
    if retailer_key not in _CLEANUP_INDEX_CONNECTIONS and await table_exists(
        conn, "guild_retailer_auto_scan_runs"
    ):
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_retailer_auto_scan_runs_ran_cleanup "
            "ON guild_retailer_auto_scan_runs (ran_at)"
        )
        _CLEANUP_INDEX_CONNECTIONS.add(retailer_key)
        changed = True

    dm_key = (connection_id, "dm_receipts")
    if dm_key not in _CLEANUP_INDEX_CONNECTIONS and await table_exists(
        conn, "user_dm_deal_alert_receipts"
    ):
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_deal_alert_receipts_delivered_cleanup "
            "ON user_dm_deal_alert_receipts (delivered_at)"
        )
        _CLEANUP_INDEX_CONNECTIONS.add(dm_key)
        changed = True

    if changed:
        await conn.commit()


async def table_exists(conn: Any, table: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (str(table),),
    )
    return await cursor.fetchone() is not None


async def delete_oldest_rows_beyond_limit(
    conn: Any,
    *,
    table: str,
    timestamp_column: str,
    keep: int,
    limit: int,
) -> int:
    """Bound a monotonic history table without scanning/counting the whole table."""
    safe_keep = max(1, int(keep))
    boundary = await conn.execute(
        f"SELECT {timestamp_column}, rowid AS {_MAINTENANCE_ROWID_ALIAS} "
        f"FROM {table} ORDER BY {timestamp_column} DESC, rowid DESC LIMIT 1 OFFSET ?",
        (safe_keep - 1,),
    )
    row = await boundary.fetchone()
    if row is None:
        return 0
    timestamp = _row_value(row, timestamp_column, 0)
    boundary_rowid = _row_value(row, _MAINTENANCE_ROWID_ALIAS, 1)
    if timestamp is None or boundary_rowid is None:
        return 0
    return await delete_bounded_rows(
        conn,
        table=table,
        where_sql=(
            f"({timestamp_column} < ? OR "
            f"({timestamp_column} = ? AND rowid < ?))"
        ),
        params=(timestamp, timestamp, int(boundary_rowid)),
        limit=limit,
        order_by_sql=f"{timestamp_column} ASC, rowid ASC",
    )


async def _bounded_rowids(
    conn: Any,
    *,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
    limit: int,
    order_by_sql: str,
) -> list[int]:
    """Select a small cleanup batch through the predicate's indexed ordering."""
    safe_limit = max(1, min(int(limit), 5_000))
    cursor = await conn.execute(
        f"SELECT rowid AS {_MAINTENANCE_ROWID_ALIAS} "
        f"FROM {table} WHERE {where_sql} ORDER BY {order_by_sql} LIMIT ?",
        (*params, safe_limit),
    )
    rows = await cursor.fetchall()
    rowids: list[int] = []
    for row in rows:
        value = _row_value(row, _MAINTENANCE_ROWID_ALIAS, 0)
        try:
            rowid = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if rowid not in rowids:
            rowids.append(rowid)
    return rowids


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


async def delete_bounded_rows(
    conn: Any,
    *,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
    limit: int,
    order_by_sql: str = "rowid ASC",
) -> int:
    """Delete at most ``limit`` selected rowids and skip no-op writes."""
    rowids = await _bounded_rowids(
        conn,
        table=table,
        where_sql=where_sql,
        params=params,
        limit=limit,
        order_by_sql=order_by_sql,
    )
    if not rowids:
        return 0
    placeholders = ",".join("?" for _ in rowids)
    await conn.execute(
        f"DELETE FROM {table} WHERE rowid IN ({placeholders})",
        tuple(rowids),
    )
    return len(rowids)


async def update_bounded_rows(
    conn: Any,
    *,
    table: str,
    set_sql: str,
    where_sql: str,
    params: tuple[Any, ...],
    limit: int,
    order_by_sql: str = "rowid ASC",
) -> int:
    """Update at most ``limit`` selected rowids and skip no-op writes."""
    rowids = await _bounded_rowids(
        conn,
        table=table,
        where_sql=where_sql,
        params=params,
        limit=limit,
        order_by_sql=order_by_sql,
    )
    if not rowids:
        return 0
    placeholders = ",".join("?" for _ in rowids)
    await conn.execute(
        f"UPDATE {table} SET {set_sql} WHERE rowid IN ({placeholders})",
        tuple(rowids),
    )
    return len(rowids)
