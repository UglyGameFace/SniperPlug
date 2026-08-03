from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.ebay_watcher.storage import (
    HEALTH_TABLE,
    LISTING_TABLE,
    RULE_TABLE,
    ensure_ebay_watcher_tables,
)
from sniperplug.services.verified_retailer_events import (
    EVENT_TABLE,
    ensure_verified_retailer_event_table,
)


@dataclass(frozen=True)
class EbayWatcherHealth:
    status: str = "not_started"
    last_successful_cycle_at: str = ""
    rules: int = 0
    enabled_rules: int = 0
    tracked_listings: int = 0
    exact_listings: int = 0
    due_rules: int = 0
    due_listings: int = 0
    pending_events: int = 0
    listing_failures: int = 0
    api_calls_since_start: int = 0
    stale: bool = True
    last_error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "healthy" and not self.stale

    def summary_line(self) -> str:
        if self.status == "healthy" and self.stale:
            state = "stale"
        elif self.ok:
            state = "healthy"
        elif self.status == "degraded":
            state = "degraded"
        else:
            state = self.status
        return (
            f"eBay watcher **{state}** • rules **{self.enabled_rules}/{self.rules} enabled** • "
            f"tracked **{self.exact_listings}/{self.tracked_listings} exact-ID** • "
            f"due rules/listings **{self.due_rules}/{self.due_listings}** • "
            f"pending fanout **{self.pending_events}** • listing failures **{self.listing_failures}** • "
            f"Browse calls since restart **{self.api_calls_since_start}**"
        )


async def load_ebay_watcher_health(db: Any) -> EbayWatcherHealth:
    await ensure_ebay_watcher_tables(db)
    await ensure_verified_retailer_event_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    state_cursor = await conn.execute(
        f"SELECT state_key, state_value FROM {HEALTH_TABLE}"
    )
    state = {
        str(_row_get(row, "state_key", 0) or ""): str(
            _row_get(row, "state_value", 1) or ""
        )
        for row in await state_cursor.fetchall()
    }
    queries = {
        "rules": f"SELECT COUNT(*) FROM {RULE_TABLE}",
        "enabled_rules": f"SELECT COUNT(*) FROM {RULE_TABLE} WHERE enabled = 1",
        "tracked_listings": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE active = 1",
        "exact_listings": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE active = 1 AND exact_identity = 1",
        "due_rules": f"SELECT COUNT(*) FROM {RULE_TABLE} WHERE enabled = 1 AND next_scan_at <= ?",
        "due_listings": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE active = 1 AND next_check_at <= ?",
        "listing_failures": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE consecutive_failures > 0",
        "pending_events": (
            f"SELECT COUNT(*) FROM {EVENT_TABLE} "
            "WHERE retailer = 'ebay' AND processed_at IS NULL"
        ),
    }
    counts: dict[str, int] = {}
    for key, sql in queries.items():
        cursor = (
            await conn.execute(sql, (now_iso,))
            if "?" in sql
            else await conn.execute(sql)
        )
        row = await cursor.fetchone()
        counts[key] = int(_row_get(row, "COUNT(*)", 0) or 0)

    status = state.get("service_status", "not_started")
    last_successful = state.get("last_successful_cycle_at", "")
    parsed = _parse_datetime(last_successful)
    stale = parsed is None or parsed < now - timedelta(minutes=20)
    try:
        api_calls = int(state.get("api_calls_since_start", "0") or 0)
    except ValueError:
        api_calls = 0
    return EbayWatcherHealth(
        status=status,
        last_successful_cycle_at=last_successful,
        rules=counts["rules"],
        enabled_rules=counts["enabled_rules"],
        tracked_listings=counts["tracked_listings"],
        exact_listings=counts["exact_listings"],
        due_rules=counts["due_rules"],
        due_listings=counts["due_listings"],
        pending_events=counts["pending_events"],
        listing_failures=counts["listing_failures"],
        api_calls_since_start=api_calls,
        stale=stale,
        last_error=(
            state.get("last_cycle_error", "")
            if status != "healthy"
            else ""
        ),
    )


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_get(row: Any, key: str, index: int) -> Any:
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
