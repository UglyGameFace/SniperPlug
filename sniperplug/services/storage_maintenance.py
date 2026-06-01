from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.autoscan_history import ensure_autoscan_history_table
from sniperplug.services.deal_feedback import ensure_deal_feedback_tables
from sniperplug.services.public_deal_posts import ensure_public_post_tables


@dataclass(frozen=True)
class MaintenanceCleanupResult:
    expired_feedback_targets: int = 0
    old_feedback_events: int = 0
    stale_active_deals_marked_expired: int = 0
    old_autoscan_reports: int = 0
    old_public_post_reservations: int = 0

    @property
    def total_changed(self) -> int:
        return (
            self.expired_feedback_targets
            + self.old_feedback_events
            + self.stale_active_deals_marked_expired
            + self.old_autoscan_reports
            + self.old_public_post_reservations
        )

    def log_fields(self) -> dict[str, int]:
        return {
            "expired_feedback_targets": self.expired_feedback_targets,
            "old_feedback_events": self.old_feedback_events,
            "stale_active_deals_marked_expired": self.stale_active_deals_marked_expired,
            "old_autoscan_reports": self.old_autoscan_reports,
            "old_public_post_reservations": self.old_public_post_reservations,
            "total_changed": self.total_changed,
        }


async def run_storage_maintenance(
    db,
    *,
    feedback_event_days: int = 90,
    active_deal_stale_hours: int = 48,
    autoscan_report_days: int = 14,
    public_reservation_hours: int = 6,
) -> MaintenanceCleanupResult:
    """Prune stale operational rows so SniperPlug stays easy to debug.

    This intentionally keeps learning summaries and active vote ledgers. It only
    removes expired button targets, old raw click history, old scan reports,
    stale active deal cache rows, and abandoned public-post reservations.
    """
    await ensure_deal_feedback_tables(db)
    await ensure_public_post_tables(db)
    await ensure_autoscan_history_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)

    expired_feedback_targets = await delete_rows(
        conn,
        "DELETE FROM guild_deal_feedback_targets WHERE expires_at <= ?",
        (now.isoformat(),),
    )
    old_feedback_events = await delete_rows(
        conn,
        "DELETE FROM guild_deal_feedback_events WHERE created_at < ?",
        ((now - timedelta(days=max(1, int(feedback_event_days)))).isoformat(),),
    )
    stale_active_deals_marked_expired = await update_rows(
        conn,
        """
        UPDATE guild_active_deal_cache
        SET status = 'expired'
        WHERE status = 'active' AND last_seen_at < ?
        """,
        ((now - timedelta(hours=max(1, int(active_deal_stale_hours)))).isoformat(),),
    )
    old_autoscan_reports = await delete_rows(
        conn,
        "DELETE FROM guild_auto_scan_report_history WHERE ran_at < ?",
        ((now - timedelta(days=max(1, int(autoscan_report_days)))).isoformat(),),
    )
    old_public_post_reservations = await delete_rows(
        conn,
        "DELETE FROM guild_public_deal_posts WHERE status = 'reserved' AND first_seen_at < ?",
        ((now - timedelta(hours=max(1, int(public_reservation_hours)))).isoformat(),),
    )

    await conn.commit()
    return MaintenanceCleanupResult(
        expired_feedback_targets=expired_feedback_targets,
        old_feedback_events=old_feedback_events,
        stale_active_deals_marked_expired=stale_active_deals_marked_expired,
        old_autoscan_reports=old_autoscan_reports,
        old_public_post_reservations=old_public_post_reservations,
    )


async def delete_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    cursor = await conn.execute(sql, params)
    return row_count(cursor)


async def update_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    cursor = await conn.execute(sql, params)
    return row_count(cursor)


def row_count(cursor: Any) -> int:
    try:
        return max(0, int(getattr(cursor, "rowcount", 0) or 0))
    except (TypeError, ValueError):
        return 0
