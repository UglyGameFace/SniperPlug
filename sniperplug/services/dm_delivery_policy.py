from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    DmDealMatchDecision,
    RECEIPT_RETENTION_DAYS,
    RECEIPTS_TABLE,
    UNLIMITED_MAX_ALERTS_PER_DAY,
    dm_alerts_sent_today,
    ensure_dm_deal_alert_tables,
)
from sniperplug.services.dm_personal_categories import split_category_preferences
from sniperplug.services.dm_deal_routes import split_dm_route_preferences


FAMILY_RECEIPTS_TABLE = "user_dm_deal_alert_family_receipts"
_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()
_USER_LOCKS: dict[int, asyncio.Lock] = {}

_FAMILY_BY_CATEGORY = {
    "desktop_computers": "tech",
    "premium_apparel": "fashion",
    "shoes_apparel": "fashion",
    "sneakers": "fashion",
    "brand_direct_electronics": "tech",
    "apple": "tech",
    "gpus": "tech",
    "cpus": "tech",
    "ram": "tech",
    "ssds": "tech",
    "mobile_accessories": "tech",
    "smart_home": "tech",
    "home_kitchen": "home",
    "appliances": "home",
    "office_school": "home",
    "household_essentials": "essentials",
    "grocery_pantry": "essentials",
    "baby_kids": "essentials",
    "pet_supplies": "essentials",
    "health_wellness": "essentials",
    "fragrance_beauty": "style",
    "gold_jewelry": "style",
    "watches": "style",
    "motor_oil": "auto_tools",
    "tools": "auto_tools",
    "outdoor_sports": "outdoor",
    "toys_collectibles": "toys",
    "seasonal_holiday": "seasonal",
    "open_box_restored": "open_box",
    "business_bulk": "business",
    "walmart_cash": "cash",
}


@dataclass(frozen=True)
class DmDeliveryGateDecision:
    allowed: bool
    reason: str
    category_family: str
    sent_today: int = 0
    family_sent_today: int = 0
    family_limit: int | None = None


def dm_delivery_lock(user_id: int) -> asyncio.Lock:
    """Serialize cap/diversity decisions and receipt writes for one subscriber."""

    key = int(user_id)
    lock = _USER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _USER_LOCKS[key] = lock
    return lock


def category_family_for_key(category_key: str) -> str:
    key = str(category_key or "uncategorized").strip().lower() or "uncategorized"
    return _FAMILY_BY_CATEGORY.get(key, key)


def family_limit_for_daily_cap(daily_cap: int, category_family: str) -> int | None:
    """Reserve finite Smart-mode slots so one family cannot consume the day.

    Fashion is intentionally tighter because apparel, shoes, and sneakers are
    high-volume markdown categories that were crowding out later deal families.
    Other families retain a larger share while still leaving room for variety.
    """

    cap = int(daily_cap)
    if cap == UNLIMITED_MAX_ALERTS_PER_DAY:
        return None
    cap = max(1, cap)
    share_percent = 28 if category_family == "fashion" else 45
    return max(1, min(cap, (cap * share_percent + 99) // 100))


def diversity_applies(
    preference: DmDealAlertPreference,
    decision: DmDealMatchDecision,
) -> bool:
    pref = preference.normalized()
    if pref.max_alerts_per_day == UNLIMITED_MAX_ALERTS_PER_DAY:
        return False
    if pref.mode != "smart" or not decision.matched or decision.priority_override:
        return False

    if split_dm_route_preferences(pref.categories):
        return False
    selected_categories, favorite_categories = split_category_preferences(
        pref.categories
    )
    if selected_categories:
        return False
    if decision.category_key in favorite_categories:
        return False
    return True


async def ensure_dm_delivery_policy_tables(db: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        await ensure_dm_deal_alert_tables(db)
        conn = db.require_conn()
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {FAMILY_RECEIPTS_TABLE} (
                user_id TEXT NOT NULL,
                deal_key TEXT NOT NULL,
                category_family TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (user_id, deal_key)
            )
            """
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{FAMILY_RECEIPTS_TABLE}_daily_family "
            f"ON {FAMILY_RECEIPTS_TABLE} (user_id, delivered_at, category_family)"
        )
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=RECEIPT_RETENTION_DAYS)
        ).isoformat()
        await conn.execute(
            f"DELETE FROM {FAMILY_RECEIPTS_TABLE} WHERE delivered_at < ?",
            (cutoff,),
        )
        await conn.commit()
        _SCHEMA_READY = True


async def dm_family_alerts_sent_today(
    db: Any,
    *,
    user_id: int,
    category_family: str,
) -> int:
    await ensure_dm_delivery_policy_tables(db)
    conn = db.require_conn()
    today = datetime.now(timezone.utc).date().isoformat()
    cursor = await conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM {FAMILY_RECEIPTS_TABLE}
        WHERE user_id = ?
          AND substr(delivered_at, 1, 10) = ?
          AND category_family = ?
        """,
        (str(int(user_id)), today, str(category_family)),
    )
    row = await cursor.fetchone()
    return _row_count(row)


async def evaluate_dm_delivery_gate(
    db: Any,
    *,
    preference: DmDealAlertPreference,
    decision: DmDealMatchDecision,
) -> DmDeliveryGateDecision:
    """Apply the shared post-match daily-cap and broad-Smart diversity policy."""

    pref = preference.normalized()
    family = category_family_for_key(decision.category_key)

    if pref.max_alerts_per_day == UNLIMITED_MAX_ALERTS_PER_DAY:
        return DmDeliveryGateDecision(True, "unlimited", family)

    sent_today = await dm_alerts_sent_today(db, pref.user_id)
    if sent_today >= pref.max_alerts_per_day:
        return DmDeliveryGateDecision(
            False,
            "daily_cap",
            family,
            sent_today=sent_today,
        )

    if not diversity_applies(pref, decision):
        return DmDeliveryGateDecision(
            True,
            "allowed",
            family,
            sent_today=sent_today,
        )

    family_limit = family_limit_for_daily_cap(pref.max_alerts_per_day, family)
    if family_limit is None:
        return DmDeliveryGateDecision(
            True,
            "allowed",
            family,
            sent_today=sent_today,
        )
    family_sent = await dm_family_alerts_sent_today(
        db,
        user_id=pref.user_id,
        category_family=family,
    )
    if family_sent >= family_limit:
        return DmDeliveryGateDecision(
            False,
            "diversity_cap",
            family,
            sent_today=sent_today,
            family_sent_today=family_sent,
            family_limit=family_limit,
        )
    return DmDeliveryGateDecision(
        True,
        "allowed",
        family,
        sent_today=sent_today,
        family_sent_today=family_sent,
        family_limit=family_limit,
    )


async def record_dm_delivery_receipt(
    db: Any,
    *,
    user_id: int,
    deal_key: str,
    category_family: str,
) -> None:
    """Persist canonical dedupe and family history in one transaction."""

    await ensure_dm_delivery_policy_tables(db)
    conn = db.require_conn()
    delivered_at = datetime.now(timezone.utc).isoformat()
    user_key = str(int(user_id))
    deal = str(deal_key)
    family = category_family_for_key(category_family)
    await conn.execute(
        f"""
        INSERT INTO {RECEIPTS_TABLE} (user_id, deal_key, delivered_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, deal_key) DO NOTHING
        """,
        (user_key, deal, delivered_at),
    )
    await conn.execute(
        f"""
        INSERT INTO {FAMILY_RECEIPTS_TABLE} (
            user_id, deal_key, category_family, delivered_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, deal_key) DO NOTHING
        """,
        (user_key, deal, family, delivered_at),
    )
    await conn.commit()


async def delete_dm_delivery_history(db: Any, *, user_id: int) -> None:
    """Remove diversity history when a subscriber explicitly deletes DM settings."""

    await ensure_dm_delivery_policy_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"DELETE FROM {FAMILY_RECEIPTS_TABLE} WHERE user_id = ?",
        (str(int(user_id)),),
    )
    await conn.commit()


def _row_count(row: Any) -> int:
    if row is None:
        return 0
    for key in ("total", 0):
        try:
            return int(row[key] or 0)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    try:
        return int(getattr(row, "total", 0) or 0)
    except (TypeError, ValueError):
        return 0
