from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.active_deal_history import prune_active_deal_history
from sniperplug.services.autoscan_history import ensure_autoscan_history_table
from sniperplug.services.deal_feedback import ensure_deal_feedback_tables
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.walmart_recheck_audit import prune_walmart_recheck_audit


OPERATIONAL_CLEANUP_BATCH_SIZE = 500
ACTIVE_DEAL_TERMINAL_RETENTION_DAYS = 7
SCAN_RUN_RETENTION_DAYS = 30
_MAINTENANCE_ROWID_ALIAS = "__sniperplug_rowid"


@dataclass(frozen=True)
class MaintenanceCleanupResult:
    expired_feedback_targets: int = 0
    old_feedback_events: int = 0
    stale_active_deals_marked_expired: int = 0
    old_inactive_active_deals: int = 0
    old_autoscan_reports: int = 0
    old_public_post_reservations: int = 0
    expired_provider_cache: int = 0
    expired_scan_result_cache: int = 0
    expired_alert_dedupe: int = 0
    expired_store_cache: int = 0
    old_scan_runs: int = 0
    old_active_deal_history: int = 0
    old_walmart_recheck_audit: int = 0

    @property
    def total_changed(self) -> int:
        return (
            self.expired_feedback_targets
            + self.old_feedback_events
            + self.stale_active_deals_marked_expired
            + self.old_inactive_active_deals
            + self.old_autoscan_reports
            + self.old_public_post_reservations
            + self.expired_provider_cache
            + self.expired_scan_result_cache
            + self.expired_alert_dedupe
            + self.expired_store_cache
            + self.old_scan_runs
            + self.old_active_deal_history
            + self.old_walmart_recheck_audit
        )

    def log_fields(self) -> dict[str, int]:
        return {
            "expired_feedback_targets": self.expired_feedback_targets,
            "old_feedback_events": self.old_feedback_events,
            "stale_active_deals_marked_expired": self.stale_active_deals_marked_expired,
            "old_inactive_active_deals": self.old_inactive_active_deals,
            "old_autoscan_reports": self.old_autoscan_reports,
            "old_public_post_reservations": self.old_public_post_reservations,
            "expired_provider_cache": self.expired_provider_cache,
            "expired_scan_result_cache": self.expired_scan_result_cache,
            "expired_alert_dedupe": self.expired_alert_dedupe,
            "expired_store_cache": self.expired_store_cache,
            "old_scan_runs": self.old_scan_runs,
            "old_active_deal_history": self.old_active_deal_history,
            "old_walmart_recheck_audit": self.old_walmart_recheck_audit,
            "total_changed": self.total_changed,
        }


async def run_storage_maintenance(
    db,
    *,
    feedback_event_days: int = 90,
    active_deal_stale_hours: int = 48,
    active_deal_terminal_days: int = ACTIVE_DEAL_TERMINAL_RETENTION_DAYS,
    autoscan_report_days: int = 14,
    public_reservation_hours: int = 6,
    scan_run_days: int = SCAN_RUN_RETENTION_DAYS,
    cleanup_batch_size: int = OPERATIONAL_CLEANUP_BATCH_SIZE,
) -> MaintenanceCleanupResult:
    """Prune disposable operational state without deleting price history.

    Cleanup is intentionally bounded so a large backlog cannot turn one startup
    into a huge Turso write burst. Product identity, ``price_observations`` and
    exact Walmart offer-price memory are never deleted here. Stale/expired deal
    cards are disposable hot-cache rows: their lifecycle transition is captured
    by the active-deal history trigger before the old cache row is removed.
    """
    await ensure_deal_feedback_tables(db)
    await ensure_public_post_tables(db)
    await ensure_autoscan_history_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    batch_size = max(1, min(int(cleanup_batch_size), 5_000))

    expired_feedback_targets = await delete_bounded_rows(
        conn,
        table="guild_deal_feedback_targets",
        where_sql="expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
    )
    old_feedback_events = await delete_bounded_rows(
        conn,
        table="guild_deal_feedback_events",
        where_sql="created_at < ?",
        params=((now - timedelta(days=max(1, int(feedback_event_days)))).isoformat(),),
        limit=batch_size,
    )
    stale_active_deals_marked_expired = await update_bounded_rows(
        conn,
        table="guild_active_deal_cache",
        set_sql="status = 'expired'",
        where_sql="status IN ('active', 'stale') AND last_seen_at < ?",
        params=((now - timedelta(hours=max(1, int(active_deal_stale_hours)))).isoformat(),),
        limit=batch_size,
    )
    old_inactive_active_deals = await delete_bounded_rows(
        conn,
        table="guild_active_deal_cache",
        where_sql="status IN ('stale', 'expired') AND last_seen_at < ?",
        params=((now - timedelta(days=max(1, int(active_deal_terminal_days)))).isoformat(),),
        limit=batch_size,
    )
    old_autoscan_reports = await delete_bounded_rows(
        conn,
        table="guild_auto_scan_report_history",
        where_sql="ran_at < ?",
        params=((now - timedelta(days=max(1, int(autoscan_report_days)))).isoformat(),),
        limit=batch_size,
    )
    old_public_post_reservations = await delete_bounded_rows(
        conn,
        table="guild_public_deal_posts",
        where_sql="status = 'reserved' AND first_seen_at < ?",
        params=((now - timedelta(hours=max(1, int(public_reservation_hours)))).isoformat(),),
        limit=batch_size,
    )

    # These are recreatable caches/dedupe ledgers. They are safe to remove only
    # after their own expiry time; no product price history lives in them.
    expired_provider_cache = await delete_bounded_rows(
        conn,
        table="provider_response_cache",
        where_sql="expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
    )
    expired_scan_result_cache = await delete_bounded_rows(
        conn,
        table="scan_result_cache",
        where_sql="expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
    )
    expired_alert_dedupe = await delete_bounded_rows(
        conn,
        table="alert_dedupe",
        where_sql="expires_at IS NOT NULL AND expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
    )
    expired_store_cache = await delete_bounded_rows(
        conn,
        table="store_cache",
        where_sql="expires_at IS NOT NULL AND expires_at <= ?",
        params=(now_iso,),
        limit=batch_size,
    )
    old_scan_runs = await delete_bounded_rows(
        conn,
        table="scan_runs",
        where_sql="started_at < ?",
        params=((now - timedelta(days=max(1, int(scan_run_days)))).isoformat(),),
        limit=batch_size,
    )

    operational_changed = sum(
        (
            expired_feedback_targets,
            old_feedback_events,
            stale_active_deals_marked_expired,
            old_inactive_active_deals,
            old_autoscan_reports,
            old_public_post_reservations,
            expired_provider_cache,
            expired_scan_result_cache,
            expired_alert_dedupe,
            expired_store_cache,
            old_scan_runs,
        )
    )
    if operational_changed:
        await conn.commit()
    old_active_deal_history = await prune_active_deal_history(db)
    old_walmart_recheck_audit = await prune_walmart_recheck_audit(db)
    return MaintenanceCleanupResult(
        expired_feedback_targets=expired_feedback_targets,
        old_feedback_events=old_feedback_events,
        stale_active_deals_marked_expired=stale_active_deals_marked_expired,
        old_inactive_active_deals=old_inactive_active_deals,
        old_autoscan_reports=old_autoscan_reports,
        old_public_post_reservations=old_public_post_reservations,
        expired_provider_cache=expired_provider_cache,
        expired_scan_result_cache=expired_scan_result_cache,
        expired_alert_dedupe=expired_alert_dedupe,
        expired_store_cache=expired_store_cache,
        old_scan_runs=old_scan_runs,
        old_active_deal_history=old_active_deal_history,
        old_walmart_recheck_audit=old_walmart_recheck_audit,
    )


async def _bounded_rowids(
    conn: Any,
    *,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
    limit: int,
) -> list[int]:
    """Select rowids through a stable alias across SQLite/libSQL row shapes.

    Tables with ``INTEGER PRIMARY KEY`` may expose ``SELECT rowid`` under the
    primary-key column name after the process-isolated libSQL worker normalizes
    result metadata into mappings. An explicit alias keeps the wire shape
    deterministic while the positional fallback preserves local aiosqlite use.
    """

    safe_limit = max(1, min(int(limit), 5_000))
    cursor = await conn.execute(
        f"SELECT rowid AS {_MAINTENANCE_ROWID_ALIAS} "
        f"FROM {table} WHERE {where_sql} ORDER BY rowid LIMIT ?",
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
) -> int:
    """Delete at most ``limit`` selected rowids and skip no-op writes."""

    rowids = await _bounded_rowids(
        conn,
        table=table,
        where_sql=where_sql,
        params=params,
        limit=limit,
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
) -> int:
    """Update at most ``limit`` selected rowids and skip no-op writes."""

    rowids = await _bounded_rowids(
        conn,
        table=table,
        where_sql=where_sql,
        params=params,
        limit=limit,
    )
    if not rowids:
        return 0
    placeholders = ",".join("?" for _ in rowids)
    await conn.execute(
        f"UPDATE {table} SET {set_sql} WHERE rowid IN ({placeholders})",
        tuple(rowids),
    )
    return len(rowids)
