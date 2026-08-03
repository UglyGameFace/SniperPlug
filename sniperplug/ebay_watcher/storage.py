from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Iterable

from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.models import (
    EbayListing,
    EbayWatchRule,
    ListingHistory,
    TrackedListingTarget,
)


RULE_TABLE = "ebay_watch_rules"
LISTING_TABLE = "ebay_watched_listings"
OBSERVATION_TABLE = "ebay_price_observations"
HEALTH_TABLE = "ebay_watcher_health"


async def ensure_ebay_watcher_tables(db: Any) -> None:
    if getattr(db, "_ebay_watcher_tables_ready", False):
        return
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {RULE_TABLE} (
            rule_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            query TEXT NOT NULL DEFAULT '',
            category_id TEXT NOT NULL DEFAULT '',
            gtin TEXT NOT NULL DEFAULT '',
            epid TEXT NOT NULL DEFAULT '',
            seller TEXT NOT NULL DEFAULT '',
            sought_after INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 50,
            min_discount_percent INTEGER NOT NULL DEFAULT 69,
            min_reference_price_cents INTEGER NOT NULL DEFAULT 20000,
            allowed_conditions_json TEXT NOT NULL DEFAULT '[]',
            min_seller_feedback_percentage REAL NOT NULL DEFAULT 97,
            min_seller_feedback_score INTEGER NOT NULL DEFAULT 10,
            search_limit INTEGER NOT NULL DEFAULT 100,
            scan_interval_seconds INTEGER NOT NULL DEFAULT 300,
            next_scan_at TEXT NOT NULL,
            last_scan_at TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LISTING_TABLE} (
            item_id TEXT PRIMARY KEY,
            legacy_item_id TEXT NOT NULL DEFAULT '',
            rule_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL DEFAULT '',
            exact_identity INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            product_url TEXT NOT NULL,
            image_url TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT 'USD',
            item_price_cents INTEGER NOT NULL,
            shipping_price_cents INTEGER,
            delivered_price_cents INTEGER,
            shipping_known INTEGER NOT NULL DEFAULT 0,
            marketing_original_price_cents INTEGER,
            baseline_price_cents INTEGER,
            baseline_observation_count INTEGER NOT NULL DEFAULT 0,
            baseline_first_seen_at TEXT,
            condition_id TEXT NOT NULL DEFAULT '',
            condition_name TEXT NOT NULL DEFAULT '',
            condition_bucket TEXT NOT NULL DEFAULT 'unknown',
            seller_id TEXT NOT NULL DEFAULT '',
            seller_feedback_percentage REAL,
            seller_feedback_score INTEGER,
            buying_options_json TEXT NOT NULL DEFAULT '[]',
            item_creation_date TEXT NOT NULL DEFAULT '',
            item_end_date TEXT NOT NULL DEFAULT '',
            availability_status TEXT NOT NULL DEFAULT '',
            gtin TEXT NOT NULL DEFAULT '',
            epid TEXT NOT NULL DEFAULT '',
            brand TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            mpn TEXT NOT NULL DEFAULT '',
            aspects_json TEXT NOT NULL DEFAULT '{{}}',
            short_description TEXT NOT NULL DEFAULT '',
            suspicious_reason TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_checked_at TEXT NOT NULL,
            next_check_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            last_alert_price_cents INTEGER,
            last_event_key TEXT NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {OBSERVATION_TABLE} (
            observation_key TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            item_price_cents INTEGER NOT NULL,
            shipping_price_cents INTEGER,
            delivered_price_cents INTEGER,
            condition_bucket TEXT NOT NULL,
            seller_id TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HEALTH_TABLE} (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{RULE_TABLE}_due "
        f"ON {RULE_TABLE} (enabled, next_scan_at, sought_after, priority)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{LISTING_TABLE}_due "
        f"ON {LISTING_TABLE} (active, next_check_at, rule_id)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{LISTING_TABLE}_fingerprint "
        f"ON {LISTING_TABLE} (fingerprint, condition_bucket)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{OBSERVATION_TABLE}_item "
        f"ON {OBSERVATION_TABLE} (item_id, observed_at)"
    )
    await conn.commit()
    try:
        setattr(db, "_ebay_watcher_tables_ready", True)
    except Exception:
        pass


async def seed_default_watch_rules(
    db: Any,
    settings: EbayWatcherSettings,
) -> int:
    await ensure_ebay_watcher_tables(db)
    inserted = 0
    defaults = (
        (
            settings.sought_after_queries,
            True,
            settings.sought_after_min_reference_price,
            settings.default_rule_interval_seconds,
            "High demand",
            100,
        ),
        (
            settings.big_ticket_queries,
            False,
            settings.big_ticket_min_reference_price,
            settings.big_ticket_rule_interval_seconds,
            "Big ticket",
            70,
        ),
    )
    for queries, sought_after, floor, interval, prefix, priority_start in defaults:
        for position, query in enumerate(queries):
            clean_query = " ".join(str(query or "").split())
            if not clean_query:
                continue
            rule = EbayWatchRule(
                rule_id=default_rule_id(f"{prefix}:{clean_query}"),
                label=f"{prefix}: {clean_query}"[:120],
                query=clean_query,
                sought_after=sought_after,
                priority=max(40, priority_start - position),
                min_discount_percent=settings.default_min_discount_percent,
                min_reference_price=floor,
                allowed_conditions=settings.allowed_conditions,
                min_seller_feedback_percentage=settings.minimum_seller_feedback_percentage,
                min_seller_feedback_score=settings.minimum_seller_feedback_score,
                search_limit=settings.search_limit,
                scan_interval_seconds=interval,
            )
            inserted += int(await save_watch_rule(db, rule, create_only=True))
    return inserted


async def save_watch_rule(
    db: Any,
    rule: EbayWatchRule,
    *,
    create_only: bool = False,
    now: datetime | None = None,
) -> bool:
    await ensure_ebay_watcher_tables(db)
    normalized = normalize_watch_rule(rule)
    if not normalized.has_search_identity:
        raise ValueError("An eBay watch rule needs a query, category, GTIN, ePID, or seller.")
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    next_scan = normalized.next_scan_at or now_iso
    if create_only:
        existing = await conn.execute(
            f"SELECT 1 FROM {RULE_TABLE} WHERE rule_id = ? LIMIT 1",
            (normalized.rule_id,),
        )
        if await existing.fetchone() is not None:
            return False

    await conn.execute(
        f"""
        INSERT INTO {RULE_TABLE} (
            rule_id, label, query, category_id, gtin, epid, seller,
            sought_after, enabled, priority, min_discount_percent,
            min_reference_price_cents, allowed_conditions_json,
            min_seller_feedback_percentage, min_seller_feedback_score,
            search_limit, scan_interval_seconds, next_scan_at, last_scan_at,
            consecutive_failures, last_error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rule_id) DO UPDATE SET
            label = excluded.label,
            query = excluded.query,
            category_id = excluded.category_id,
            gtin = excluded.gtin,
            epid = excluded.epid,
            seller = excluded.seller,
            sought_after = excluded.sought_after,
            enabled = excluded.enabled,
            priority = excluded.priority,
            min_discount_percent = excluded.min_discount_percent,
            min_reference_price_cents = excluded.min_reference_price_cents,
            allowed_conditions_json = excluded.allowed_conditions_json,
            min_seller_feedback_percentage = excluded.min_seller_feedback_percentage,
            min_seller_feedback_score = excluded.min_seller_feedback_score,
            search_limit = excluded.search_limit,
            scan_interval_seconds = excluded.scan_interval_seconds,
            next_scan_at = excluded.next_scan_at,
            updated_at = excluded.updated_at
        """,
        (
            normalized.rule_id,
            normalized.label,
            normalized.query,
            normalized.category_id,
            normalized.gtin,
            normalized.epid,
            normalized.seller,
            int(normalized.sought_after),
            int(normalized.enabled),
            normalized.priority,
            normalized.min_discount_percent,
            _to_cents(normalized.min_reference_price),
            json.dumps(list(normalized.allowed_conditions)),
            normalized.min_seller_feedback_percentage,
            normalized.min_seller_feedback_score,
            normalized.search_limit,
            normalized.scan_interval_seconds,
            next_scan,
            normalized.last_scan_at or None,
            normalized.consecutive_failures,
            _compact(normalized.last_error, 800),
            now_iso,
            now_iso,
        ),
    )
    await conn.commit()
    return True


async def list_watch_rules(
    db: Any,
    *,
    include_disabled: bool = True,
) -> list[EbayWatchRule]:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    sql = f"SELECT * FROM {RULE_TABLE}"
    params: tuple[Any, ...] = ()
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY sought_after DESC, priority DESC, label ASC"
    cursor = await conn.execute(sql, params)
    return [_rule_from_row(row) for row in await cursor.fetchall()]


async def get_watch_rule(db: Any, rule_id: str) -> EbayWatchRule | None:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT * FROM {RULE_TABLE} WHERE rule_id = ? LIMIT 1",
        (str(rule_id or "").strip(),),
    )
    row = await cursor.fetchone()
    return _rule_from_row(row) if row is not None else None


async def delete_watch_rule(db: Any, rule_id: str) -> bool:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    clean_id = str(rule_id or "").strip()
    cursor = await conn.execute(
        f"SELECT 1 FROM {RULE_TABLE} WHERE rule_id = ? LIMIT 1",
        (clean_id,),
    )
    if await cursor.fetchone() is None:
        return False
    await conn.execute(f"DELETE FROM {RULE_TABLE} WHERE rule_id = ?", (clean_id,))
    await conn.execute(
        f"UPDATE {LISTING_TABLE} SET active = 0, last_error = ? WHERE rule_id = ?",
        ("watch rule deleted", clean_id),
    )
    await conn.commit()
    return True


async def set_watch_rule_enabled(db: Any, rule_id: str, enabled: bool) -> bool:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    clean_id = str(rule_id or "").strip()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await conn.execute(
        f"SELECT 1 FROM {RULE_TABLE} WHERE rule_id = ? LIMIT 1",
        (clean_id,),
    )
    if await cursor.fetchone() is None:
        return False
    await conn.execute(
        f"""
        UPDATE {RULE_TABLE}
        SET enabled = ?, next_scan_at = ?, updated_at = ?
        WHERE rule_id = ?
        """,
        (int(enabled), now, now, clean_id),
    )
    await conn.commit()
    return True


async def claim_due_watch_rules(
    db: Any,
    *,
    limit: int,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> list[EbayWatchRule]:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT * FROM {RULE_TABLE}
        WHERE enabled = 1 AND next_scan_at <= ?
        ORDER BY sought_after DESC, priority DESC,
                 COALESCE(last_scan_at, '') ASC, next_scan_at ASC
        LIMIT ?
        """,
        (now_iso, max(1, int(limit))),
    )
    rows = await cursor.fetchall()
    rules = [_rule_from_row(row) for row in rows]
    if rules:
        next_scan = (now_dt + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        placeholders = ",".join("?" for _ in rules)
        await conn.execute(
            f"UPDATE {RULE_TABLE} SET next_scan_at = ? "
            f"WHERE rule_id IN ({placeholders})",
            (next_scan, *(rule.rule_id for rule in rules)),
        )
        await conn.commit()
    return rules


async def complete_watch_rule(
    db: Any,
    rule: EbayWatchRule,
    *,
    now: datetime | None = None,
) -> None:
    conn = db.require_conn()
    now_dt = _utc(now)
    await conn.execute(
        f"""
        UPDATE {RULE_TABLE}
        SET last_scan_at = ?, next_scan_at = ?, consecutive_failures = 0,
            last_error = '', updated_at = ?
        WHERE rule_id = ?
        """,
        (
            now_dt.isoformat(),
            (now_dt + timedelta(seconds=rule.scan_interval_seconds)).isoformat(),
            now_dt.isoformat(),
            rule.rule_id,
        ),
    )
    await conn.commit()


async def fail_watch_rule(
    db: Any,
    rule: EbayWatchRule,
    *,
    error: str,
    retry_seconds: int,
    now: datetime | None = None,
) -> None:
    conn = db.require_conn()
    now_dt = _utc(now)
    delay = min(
        3600,
        max(30, int(retry_seconds)) * (2 ** min(4, rule.consecutive_failures)),
    )
    await conn.execute(
        f"""
        UPDATE {RULE_TABLE}
        SET last_scan_at = ?, next_scan_at = ?,
            consecutive_failures = consecutive_failures + 1,
            last_error = ?, updated_at = ?
        WHERE rule_id = ?
        """,
        (
            now_dt.isoformat(),
            (now_dt + timedelta(seconds=delay)).isoformat(),
            _compact(error, 800),
            now_dt.isoformat(),
            rule.rule_id,
        ),
    )
    await conn.commit()


async def claim_due_tracked_listings(
    db: Any,
    *,
    limit: int,
    lease_seconds: int = 120,
    now: datetime | None = None,
) -> list[TrackedListingTarget]:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT item_id, rule_id, next_check_at, consecutive_failures
        FROM {LISTING_TABLE}
        WHERE active = 1 AND next_check_at <= ?
        ORDER BY consecutive_failures ASC, next_check_at ASC
        LIMIT ?
        """,
        (now_iso, max(1, min(20, int(limit)))),
    )
    targets = [
        TrackedListingTarget(
            item_id=str(_row_get(row, "item_id", 0) or ""),
            rule_id=str(_row_get(row, "rule_id", 1) or ""),
            next_check_at=str(_row_get(row, "next_check_at", 2) or ""),
            consecutive_failures=int(_row_get(row, "consecutive_failures", 3) or 0),
        )
        for row in await cursor.fetchall()
        if str(_row_get(row, "item_id", 0) or "")
    ]
    if targets:
        lease_until = (now_dt + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        placeholders = ",".join("?" for _ in targets)
        await conn.execute(
            f"UPDATE {LISTING_TABLE} SET next_check_at = ? "
            f"WHERE item_id IN ({placeholders})",
            (lease_until, *(target.item_id for target in targets)),
        )
        await conn.commit()
    return targets


async def store_listing_observation(
    db: Any,
    *,
    listing: EbayListing,
    rule: EbayWatchRule,
    next_check_delay: timedelta,
    now: datetime | None = None,
) -> ListingHistory:
    await ensure_ebay_watcher_tables(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT first_seen_at, delivered_price_cents, baseline_price_cents,
               baseline_observation_count, baseline_first_seen_at,
               last_alert_price_cents, last_event_key
        FROM {LISTING_TABLE}
        WHERE item_id = ?
        LIMIT 1
        """,
        (listing.item_id,),
    )
    row = await cursor.fetchone()
    current_cents = _to_cents_or_none(listing.delivered_price)
    previous_cents = _int_or_none(_row_get(row, "delivered_price_cents", 1))
    prior_baseline_cents = _int_or_none(_row_get(row, "baseline_price_cents", 2))
    prior_baseline_count = int(_row_get(row, "baseline_observation_count", 3) or 0)
    prior_baseline_first = str(
        _row_get(row, "baseline_first_seen_at", 4) or ""
    )
    first_seen = str(_row_get(row, "first_seen_at", 0) or now_iso)
    is_new = row is None

    baseline_cents = prior_baseline_cents
    baseline_count = prior_baseline_count
    baseline_first = prior_baseline_first or now_iso
    if current_cents is not None:
        if baseline_cents is None:
            baseline_cents = current_cents
            baseline_count = 1
            baseline_first = now_iso
        elif current_cents > int(round(baseline_cents * 1.02)):
            baseline_cents = current_cents
            baseline_count = 1
            baseline_first = now_iso
        elif current_cents >= int(round(baseline_cents * 0.98)):
            baseline_count = max(1, baseline_count + 1)

    observation_key = sha256(
        (
            f"{listing.item_id}|{_to_cents(listing.item_price)}|"
            f"{_to_cents_or_none(listing.shipping_price)}|{current_cents}|"
            f"{listing.condition_bucket}|{now_dt.strftime('%Y-%m-%dT%H')}"
        ).encode("utf-8")
    ).hexdigest()
    await conn.execute(
        f"""
        INSERT INTO {OBSERVATION_TABLE} (
            observation_key, item_id, item_price_cents, shipping_price_cents,
            delivered_price_cents, condition_bucket, seller_id, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_key) DO NOTHING
        """,
        (
            observation_key,
            listing.item_id,
            _to_cents(listing.item_price),
            _to_cents_or_none(listing.shipping_price),
            current_cents,
            listing.condition_bucket,
            listing.seller_id,
            now_iso,
        ),
    )
    await conn.execute(
        f"""
        INSERT INTO {LISTING_TABLE} (
            item_id, legacy_item_id, rule_id, fingerprint, exact_identity,
            title, product_url, image_url, currency, item_price_cents,
            shipping_price_cents, delivered_price_cents, shipping_known,
            marketing_original_price_cents, baseline_price_cents,
            baseline_observation_count, baseline_first_seen_at,
            condition_id, condition_name, condition_bucket, seller_id,
            seller_feedback_percentage, seller_feedback_score,
            buying_options_json, item_creation_date, item_end_date,
            availability_status, gtin, epid, brand, model, mpn, aspects_json,
            short_description, suspicious_reason, first_seen_at, last_seen_at,
            last_checked_at, next_check_at, active, consecutive_failures,
            last_error, last_alert_price_cents, last_event_key
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '',
            ?, ?
        )
        ON CONFLICT(item_id) DO UPDATE SET
            legacy_item_id = excluded.legacy_item_id,
            rule_id = excluded.rule_id,
            fingerprint = excluded.fingerprint,
            exact_identity = excluded.exact_identity,
            title = excluded.title,
            product_url = excluded.product_url,
            image_url = excluded.image_url,
            currency = excluded.currency,
            item_price_cents = excluded.item_price_cents,
            shipping_price_cents = excluded.shipping_price_cents,
            delivered_price_cents = excluded.delivered_price_cents,
            shipping_known = excluded.shipping_known,
            marketing_original_price_cents = excluded.marketing_original_price_cents,
            baseline_price_cents = excluded.baseline_price_cents,
            baseline_observation_count = excluded.baseline_observation_count,
            baseline_first_seen_at = excluded.baseline_first_seen_at,
            condition_id = excluded.condition_id,
            condition_name = excluded.condition_name,
            condition_bucket = excluded.condition_bucket,
            seller_id = excluded.seller_id,
            seller_feedback_percentage = excluded.seller_feedback_percentage,
            seller_feedback_score = excluded.seller_feedback_score,
            buying_options_json = excluded.buying_options_json,
            item_creation_date = excluded.item_creation_date,
            item_end_date = excluded.item_end_date,
            availability_status = excluded.availability_status,
            gtin = excluded.gtin,
            epid = excluded.epid,
            brand = excluded.brand,
            model = excluded.model,
            mpn = excluded.mpn,
            aspects_json = excluded.aspects_json,
            short_description = excluded.short_description,
            suspicious_reason = excluded.suspicious_reason,
            last_seen_at = excluded.last_seen_at,
            last_checked_at = excluded.last_checked_at,
            next_check_at = excluded.next_check_at,
            active = excluded.active,
            consecutive_failures = 0,
            last_error = ''
        """,
        (
            listing.item_id,
            listing.legacy_item_id,
            rule.rule_id,
            listing.fingerprint,
            int(listing.exact_identity),
            listing.title,
            listing.product_url,
            listing.image_url,
            listing.currency,
            _to_cents(listing.item_price),
            _to_cents_or_none(listing.shipping_price),
            current_cents,
            int(listing.shipping_known),
            _to_cents_or_none(listing.marketing_original_price),
            baseline_cents,
            baseline_count,
            baseline_first,
            listing.condition_id,
            listing.condition_name,
            listing.condition_bucket,
            listing.seller_id,
            listing.seller_feedback_percentage,
            listing.seller_feedback_score,
            json.dumps(list(listing.buying_options)),
            listing.item_creation_date,
            listing.item_end_date,
            listing.estimated_availability_status,
            listing.gtin,
            listing.epid,
            listing.brand,
            listing.model,
            listing.mpn,
            json.dumps(listing.aspects, sort_keys=True),
            _compact(listing.short_description, 2000),
            _compact(listing.suspicious_reason, 300),
            first_seen,
            now_iso,
            now_iso,
            (now_dt + next_check_delay).isoformat(),
            int(listing.active),
            _int_or_none(_row_get(row, "last_alert_price_cents", 5)),
            str(_row_get(row, "last_event_key", 6) or ""),
        ),
    )
    await conn.commit()
    return ListingHistory(
        item_id=listing.item_id,
        first_seen_at=first_seen,
        previous_delivered_price=_from_cents(previous_cents),
        prior_baseline_price=_from_cents(prior_baseline_cents),
        prior_baseline_observations=prior_baseline_count,
        prior_baseline_first_seen_at=prior_baseline_first,
        last_alert_price=_from_cents(
            _int_or_none(_row_get(row, "last_alert_price_cents", 5))
        ),
        last_event_key=str(_row_get(row, "last_event_key", 6) or ""),
        is_new=is_new,
    )


async def mark_listing_event(
    db: Any,
    *,
    item_id: str,
    event_key: str,
    current_price: float,
) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        UPDATE {LISTING_TABLE}
        SET last_event_key = ?, last_alert_price_cents = ?
        WHERE item_id = ?
        """,
        (event_key, _to_cents(current_price), item_id),
    )
    await conn.commit()


async def fail_tracked_listings(
    db: Any,
    *,
    item_ids: Iterable[str],
    error: str,
    retry_seconds: int,
    deactivate: bool = False,
    now: datetime | None = None,
) -> None:
    keys = tuple(dict.fromkeys(str(value).strip() for value in item_ids if str(value).strip()))
    if not keys:
        return
    conn = db.require_conn()
    now_dt = _utc(now)
    placeholders = ",".join("?" for _ in keys)
    await conn.execute(
        f"""
        UPDATE {LISTING_TABLE}
        SET next_check_at = ?,
            consecutive_failures = consecutive_failures + 1,
            last_error = ?,
            active = CASE WHEN ? = 1 THEN 0 ELSE active END,
            last_checked_at = ?
        WHERE item_id IN ({placeholders})
        """,
        (
            (now_dt + timedelta(seconds=max(30, int(retry_seconds)))).isoformat(),
            _compact(error, 800),
            int(deactivate),
            now_dt.isoformat(),
            *keys,
        ),
    )
    await conn.commit()


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


def normalize_watch_rule(rule: EbayWatchRule) -> EbayWatchRule:
    clean_id = str(rule.rule_id or "").strip()
    if not clean_id:
        identity = "|".join(
            (
                rule.query,
                rule.category_id,
                rule.gtin,
                rule.epid,
                rule.seller,
            )
        )
        clean_id = f"ebay-rule:{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    conditions = tuple(
        dict.fromkeys(
            str(value).strip().lower()
            for value in rule.allowed_conditions
            if str(value).strip()
        )
    )
    return replace(
        rule,
        rule_id=clean_id[:100],
        label=_compact(rule.label or rule.query or clean_id, 120),
        query=_compact(rule.query, 300),
        category_id=_compact(rule.category_id, 30),
        gtin=_compact(rule.gtin, 50),
        epid=_compact(rule.epid, 50),
        seller=_compact(rule.seller, 100),
        priority=max(0, min(100, int(rule.priority))),
        min_discount_percent=max(1, min(95, int(rule.min_discount_percent))),
        min_reference_price=max(0.01, min(100000.0, float(rule.min_reference_price))),
        allowed_conditions=conditions,
        min_seller_feedback_percentage=max(
            0.0, min(100.0, float(rule.min_seller_feedback_percentage))
        ),
        min_seller_feedback_score=max(0, int(rule.min_seller_feedback_score)),
        search_limit=max(1, min(200, int(rule.search_limit))),
        scan_interval_seconds=max(60, min(86400, int(rule.scan_interval_seconds))),
    )


def default_rule_id(query: str) -> str:
    return f"ebay-default:{sha256(query.lower().encode('utf-8')).hexdigest()[:16]}"


def _rule_from_row(row: Any) -> EbayWatchRule:
    try:
        conditions = tuple(
            str(value).strip().lower()
            for value in json.loads(_row_get(row, "allowed_conditions_json", 12) or "[]")
            if str(value).strip()
        )
    except Exception:
        conditions = ()
    return EbayWatchRule(
        rule_id=str(_row_get(row, "rule_id", 0) or ""),
        label=str(_row_get(row, "label", 1) or ""),
        query=str(_row_get(row, "query", 2) or ""),
        category_id=str(_row_get(row, "category_id", 3) or ""),
        gtin=str(_row_get(row, "gtin", 4) or ""),
        epid=str(_row_get(row, "epid", 5) or ""),
        seller=str(_row_get(row, "seller", 6) or ""),
        sought_after=bool(int(_row_get(row, "sought_after", 7) or 0)),
        enabled=bool(int(_row_get(row, "enabled", 8) or 0)),
        priority=int(_row_get(row, "priority", 9) or 50),
        min_discount_percent=int(_row_get(row, "min_discount_percent", 10) or 69),
        min_reference_price=_from_cents(
            _int_or_none(_row_get(row, "min_reference_price_cents", 11))
        )
        or 200.0,
        allowed_conditions=conditions,
        min_seller_feedback_percentage=float(
            _row_get(row, "min_seller_feedback_percentage", 13) or 0.0
        ),
        min_seller_feedback_score=int(
            _row_get(row, "min_seller_feedback_score", 14) or 0
        ),
        search_limit=int(_row_get(row, "search_limit", 15) or 100),
        scan_interval_seconds=int(
            _row_get(row, "scan_interval_seconds", 16) or 300
        ),
        next_scan_at=str(_row_get(row, "next_scan_at", 17) or ""),
        last_scan_at=str(_row_get(row, "last_scan_at", 18) or ""),
        consecutive_failures=int(
            _row_get(row, "consecutive_failures", 19) or 0
        ),
        last_error=str(_row_get(row, "last_error", 20) or ""),
    )


def _to_cents(value: float) -> int:
    return int(round(float(value) * 100))


def _to_cents_or_none(value: float | None) -> int | None:
    if value is None:
        return None
    return _to_cents(value)


def _from_cents(value: int | None) -> float | None:
    if value is None:
        return None
    return round(int(value) / 100.0, 2)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


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
