from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.ebay_watcher.models import EbayAPIBudgetExceeded
from sniperplug.ebay_watcher.storage.common import _row_get, _utc
from sniperplug.ebay_watcher.storage.schema import (
    API_USAGE_TABLE,
    HEALTH_TABLE,
    LISTING_TABLE,
    RULE_TABLE,
    ensure_ebay_watcher_tables,
)


BROWSE_TOTAL_BUCKET = "browse_total"
METHOD_BUCKETS = {"browse_standard", "browse_get_items"}


async def reserve_api_call(
    db: Any,
    *,
    bucket: str,
    daily_limit: int,
    now: datetime | None = None,
) -> int:
    """Reserve one shared eBay Browse request before it is sent.

    The shared total is the enforcement counter; method-specific rows are
    diagnostics only. Persisting both prevents restarts and retries from
    silently resetting usage.
    """

    await ensure_ebay_watcher_tables(db)
    method_bucket = str(bucket or "").strip()
    if method_bucket not in METHOD_BUCKETS:
        raise ValueError(f"Unsupported eBay API budget bucket: {method_bucket}")
    limit = max(1, int(daily_limit))
    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    usage_date = now_dt.date().isoformat()

    cursor = await conn.execute(
        f"SELECT call_count FROM {API_USAGE_TABLE} "
        "WHERE usage_date = ? AND bucket = ? LIMIT 1",
        (usage_date, BROWSE_TOTAL_BUCKET),
    )
    row = await cursor.fetchone()
    total_count = int(_row_get(row, "call_count", 0) or 0)
    if total_count >= limit:
        raise EbayAPIBudgetExceeded(BROWSE_TOTAL_BUCKET, limit, usage_date)

    next_total = total_count + 1
    await conn.execute(
        f"""
        INSERT INTO {API_USAGE_TABLE} (
            usage_date, bucket, call_count, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(usage_date, bucket) DO UPDATE SET
            call_count = excluded.call_count,
            updated_at = excluded.updated_at
        """,
        (usage_date, BROWSE_TOTAL_BUCKET, next_total, now_iso),
    )
    await conn.execute(
        f"""
        INSERT INTO {API_USAGE_TABLE} (
            usage_date, bucket, call_count, updated_at
        ) VALUES (?, ?, 1, ?)
        ON CONFLICT(usage_date, bucket) DO UPDATE SET
            call_count = call_count + 1,
            updated_at = excluded.updated_at
        """,
        (usage_date, method_bucket, now_iso),
    )
    await conn.execute(
        f"DELETE FROM {API_USAGE_TABLE} WHERE usage_date < ?",
        ((now_dt - timedelta(days=14)).date().isoformat(),),
    )
    await conn.commit()
    return next_total


async def ebay_api_usage(
    db: Any,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    usage_date = _utc(now).date().isoformat()
    cursor = await conn.execute(
        f"SELECT bucket, call_count FROM {API_USAGE_TABLE} WHERE usage_date = ?",
        (usage_date,),
    )
    result = {
        BROWSE_TOTAL_BUCKET: 0,
        "browse_standard": 0,
        "browse_get_items": 0,
    }
    for row in await cursor.fetchall():
        bucket = str(_row_get(row, "bucket", 0) or "")
        if bucket in result:
            result[bucket] = int(_row_get(row, "call_count", 1) or 0)
    return result


async def set_health_value(
    db: Any,
    key: str,
    value: Any,
    *,
    now: datetime | None = None,
) -> None:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {HEALTH_TABLE} (state_key, state_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(state_key) DO UPDATE SET
            state_value = excluded.state_value,
            updated_at = excluded.updated_at
        """,
        (str(key), str(value), _utc(now).isoformat()),
    )
    await conn.commit()


async def ebay_watcher_counts(db: Any) -> dict[str, int]:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    queries = {
        "rules": f"SELECT COUNT(*) FROM {RULE_TABLE}",
        "enabled_rules": f"SELECT COUNT(*) FROM {RULE_TABLE} WHERE enabled = 1",
        "tracked_listings": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE active = 1",
        "exact_listings": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE active = 1 AND exact_identity = 1",
        "due_rules": f"SELECT COUNT(*) FROM {RULE_TABLE} WHERE enabled = 1 AND next_scan_at <= ?",
        "due_listings": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE active = 1 AND next_check_at <= ?",
        "listing_failures": f"SELECT COUNT(*) FROM {LISTING_TABLE} WHERE consecutive_failures > 0",
    }
    result: dict[str, int] = {}
    for key, sql in queries.items():
        cursor = await conn.execute(sql, (now_iso,)) if "?" in sql else await conn.execute(sql)
        row = await cursor.fetchone()
        result[key] = int(_row_get(row, "COUNT(*)", 0) or 0)
    return result
