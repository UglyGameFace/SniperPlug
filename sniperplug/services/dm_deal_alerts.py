from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sniperplug.services.dm_deal_routes import (
    personal_route_labels,
    split_dm_route_preferences,
)
from sniperplug.services.dm_product_interests import split_product_preferences
from sniperplug.services.opportunity_watchlist import category_for_title


PREFERENCES_TABLE = "user_dm_deal_alert_preferences"
RECEIPTS_TABLE = "user_dm_deal_alert_receipts"
DEFAULT_MIN_DISCOUNT = 35
DEFAULT_MIN_SCORE = 78
DEFAULT_MAX_ALERTS_PER_DAY = 25
UNLIMITED_MAX_ALERTS_PER_DAY = 0
RECEIPT_RETENTION_DAYS = 90
_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "tech": (
        "desktop_computers",
        "brand_direct_electronics",
        "apple",
        "gpus",
        "cpus",
        "ram",
        "ssds",
        "mobile_accessories",
        "smart_home",
    ),
    "gaming": ("desktop_computers", "brand_direct_electronics", "gpus", "cpus", "ram", "ssds", "toys_collectibles"),
    "home": ("home_kitchen", "appliances", "office_school", "household_essentials"),
    "essentials": ("household_essentials", "grocery_pantry", "baby_kids", "pet_supplies", "health_wellness"),
    "toys": ("toys_collectibles",),
    "auto": ("motor_oil", "tools"),
    "outdoor": ("outdoor_sports",),
    "beauty": ("fragrance_beauty", "health_wellness"),
    "style": ("fragrance_beauty", "gold_jewelry", "watches", "premium_apparel", "shoes_apparel", "sneakers"),
    "cash": ("walmart_cash",),
    "walmart_cash": ("walmart_cash",),
    "open_box": ("open_box_restored",),
}


@dataclass(frozen=True)
class DmDealAlertPreference:
    user_id: int
    enabled: bool = False
    min_discount: int = DEFAULT_MIN_DISCOUNT
    max_price_cents: int | None = None
    categories: tuple[str, ...] = ()
    max_alerts_per_day: int = DEFAULT_MAX_ALERTS_PER_DAY
    failure_count: int = 0
    last_error: str = ""

    def normalized(self) -> "DmDealAlertPreference":
        return replace(
            self,
            user_id=int(self.user_id),
            min_discount=max(0, min(95, int(self.min_discount))),
            max_price_cents=(
                max(1, int(self.max_price_cents))
                if self.max_price_cents not in (None, 0)
                else None
            ),
            categories=normalize_categories(
                _translate_legacy_category_tokens(self.categories)
            ),
            max_alerts_per_day=_normalize_daily_cap(self.max_alerts_per_day),
            failure_count=max(0, int(self.failure_count)),
            last_error=_clean_text(self.last_error, limit=500),
        )

    def summary_lines(self) -> tuple[str, ...]:
        normalized = self.normalized()
        price = (
            f"${normalized.max_price_cents / 100:,.2f}"
            if normalized.max_price_cents is not None
            else "no maximum"
        )
        categories = ", ".join(normalized.categories) if normalized.categories else "all categories"
        return (
            f"Enabled: **{'yes' if normalized.enabled else 'no'}**",
            f"Minimum discount: **{normalized.min_discount}%**",
            f"Maximum price: **{price}**",
            f"Categories: **{categories}**",
            f"Daily limit: **{daily_cap_label(normalized.max_alerts_per_day)}**",
        )

    def summary_sentence(self) -> str:
        """One plain sentence describing exactly what will DM this user."""

        normalized = self.normalized()
        what = _summary_what(normalized)
        segments = [what, _summary_quality(normalized)]
        floor_cents = _explicit_flip_floor_cents(normalized.categories)
        if floor_cents > 0 and "dm_route:price_glitch" in normalized.categories:
            segments.append(f"price mistakes worth ${floor_cents / 100:,.0f}+")
        segments.append(
            "no daily limit"
            if normalized.max_alerts_per_day == UNLIMITED_MAX_ALERTS_PER_DAY
            else f"up to {normalized.max_alerts_per_day} a day"
        )
        body = " · ".join(segments)
        if not normalized.enabled:
            return f"Deal DMs are off — turn them on and you'll get DMs for {body}."
        return f"You'll get DMs for {body}."


def _translate_legacy_category_tokens(
    values: Iterable[str] | str | None,
) -> tuple[str, ...]:
    """Drop retired category tokens from the selected-categories list.

    Favorites used to be a separate soft-priority list; they were never a
    subscription, so on read their tokens drop silently instead of folding
    into the selection. Muted categories are gone entirely, so their
    tokens drop silently instead of vetoing deals forever.
    """

    translated: list[str] = []
    for value in _split_values(values):
        if value.startswith("muted:"):
            continue
        if value.startswith("favorite:"):
            continue
        translated.append(value)
    return tuple(translated)


def _explicit_flip_floor_cents(categories: Iterable[str]) -> int:
    """The stored price-mistake profit floor, or 0 when the user never set one."""

    for token in categories:
        if token.startswith("flip_profit:"):
            try:
                return max(0, int(token[len("flip_profit:") :].strip()))
            except (TypeError, ValueError):
                return 0
    return 0


def _summary_what(normalized: "DmDealAlertPreference") -> str:
    plain_names = dict(personal_route_labels())
    parts: list[str] = []
    for route in split_dm_route_preferences(normalized.categories):
        parts.append(plain_names[route].lower())
    interests, brands = split_product_preferences(normalized.categories)
    if interests:
        parts.append("specific products you picked")
    if brands:
        parts.append("brands you picked")
    categories = [token for token in normalized.categories if ":" not in token]
    if categories:
        parts.append(
            "1 category you picked"
            if len(categories) == 1
            else f"{len(categories)} categories you picked"
        )
    if not parts:
        return "all deals"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _summary_quality(normalized: "DmDealAlertPreference") -> str:
    quality = (
        "any discount"
        if normalized.min_discount == 0
        else f"{normalized.min_discount}% off or better"
    )
    if normalized.max_price_cents is not None:
        quality += f", under ${normalized.max_price_cents / 100:,.0f}"
    return quality


@dataclass(frozen=True)
class DmDealMatchDecision:
    matched: bool
    reason: str
    category_key: str = "uncategorized"
    required_discount: int = 0
    savings_cents: int = 0
    walmart_cash_cents: int = 0


async def ensure_dm_deal_alert_tables(db: Any) -> None:
    """Initialize DM alert storage once per process, outside delivery hot paths."""

    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = db.require_conn()
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PREFERENCES_TABLE} (
                user_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'smart',
                min_discount INTEGER NOT NULL DEFAULT 35,
                max_price_cents INTEGER,
                min_score INTEGER NOT NULL DEFAULT 78,
                min_savings_cents INTEGER NOT NULL DEFAULT 0,
                categories_json TEXT NOT NULL DEFAULT '[]',
                keywords_json TEXT NOT NULL DEFAULT '[]',
                exclude_keywords_json TEXT NOT NULL DEFAULT '[]',
                walmart_cash_only INTEGER NOT NULL DEFAULT 0,
                max_alerts_per_day INTEGER NOT NULL DEFAULT 25,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {RECEIPTS_TABLE} (
                user_id TEXT NOT NULL,
                deal_key TEXT NOT NULL,
                delivered_at TEXT NOT NULL,
                PRIMARY KEY (user_id, deal_key)
            )
            """
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{RECEIPTS_TABLE}_delivered "
            f"ON {RECEIPTS_TABLE} (user_id, delivered_at)"
        )
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=RECEIPT_RETENTION_DAYS)
        ).isoformat()
        await conn.execute(
            f"DELETE FROM {RECEIPTS_TABLE} WHERE delivered_at < ?",
            (cutoff,),
        )
        await conn.commit()
        _SCHEMA_READY = True


async def get_dm_deal_alert_preference(db: Any, user_id: int) -> DmDealAlertPreference:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT user_id, enabled, min_discount, max_price_cents,
               categories_json, max_alerts_per_day, failure_count, last_error
        FROM {PREFERENCES_TABLE}
        WHERE user_id = ?
        """,
        (str(int(user_id)),),
    )
    row = await cursor.fetchone()
    if row is None:
        return DmDealAlertPreference(user_id=int(user_id))
    return _preference_from_row(row).normalized()


async def list_enabled_dm_deal_alert_preferences(db: Any) -> list[DmDealAlertPreference]:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT user_id, enabled, min_discount, max_price_cents,
               categories_json, max_alerts_per_day, failure_count, last_error
        FROM {PREFERENCES_TABLE}
        WHERE enabled = 1
        ORDER BY updated_at ASC
        """
    )
    rows = await cursor.fetchall()
    return [_preference_from_row(row).normalized() for row in rows]


async def save_dm_deal_alert_preference(db: Any, preference: DmDealAlertPreference) -> DmDealAlertPreference:
    normalized = preference.normalized()
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        INSERT INTO {PREFERENCES_TABLE} (
            user_id, enabled, mode, min_discount, max_price_cents,
            min_score, min_savings_cents, categories_json, keywords_json,
            exclude_keywords_json, walmart_cash_only, max_alerts_per_day,
            failure_count, last_error, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            enabled = excluded.enabled,
            mode = excluded.mode,
            min_discount = excluded.min_discount,
            max_price_cents = excluded.max_price_cents,
            min_score = excluded.min_score,
            min_savings_cents = excluded.min_savings_cents,
            categories_json = excluded.categories_json,
            keywords_json = excluded.keywords_json,
            exclude_keywords_json = excluded.exclude_keywords_json,
            walmart_cash_only = excluded.walmart_cash_only,
            max_alerts_per_day = excluded.max_alerts_per_day,
            failure_count = excluded.failure_count,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (
            str(normalized.user_id),
            int(normalized.enabled),
            # Legacy columns kept for schema compatibility; the features
            # behind them were removed, so they are written as inert defaults
            # and never read back.
            "custom",
            normalized.min_discount,
            normalized.max_price_cents,
            DEFAULT_MIN_SCORE,
            0,
            json.dumps(list(normalized.categories)),
            "[]",
            "[]",
            0,
            normalized.max_alerts_per_day,
            normalized.failure_count,
            normalized.last_error,
            now,
            now,
        ),
    )
    await conn.commit()
    return normalized


async def delete_dm_deal_alert_preference(db: Any, user_id: int) -> None:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    user_key = str(int(user_id))
    await conn.execute(f"DELETE FROM {PREFERENCES_TABLE} WHERE user_id = ?", (user_key,))
    await conn.execute(f"DELETE FROM {RECEIPTS_TABLE} WHERE user_id = ?", (user_key,))
    await conn.commit()


async def dm_receipt_exists(db: Any, *, user_id: int, deal_key: str) -> bool:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT 1 FROM {RECEIPTS_TABLE} WHERE user_id = ? AND deal_key = ? LIMIT 1",
        (str(int(user_id)), str(deal_key)),
    )
    return await cursor.fetchone() is not None


async def record_dm_receipt(db: Any, *, user_id: int, deal_key: str) -> None:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {RECEIPTS_TABLE} (user_id, deal_key, delivered_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, deal_key) DO NOTHING
        """,
        (str(int(user_id)), str(deal_key), datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()


async def dm_alerts_sent_today(db: Any, user_id: int) -> int:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    today = datetime.now(timezone.utc).date().isoformat()
    cursor = await conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM {RECEIPTS_TABLE}
        WHERE user_id = ? AND substr(delivered_at, 1, 10) = ?
        """,
        (str(int(user_id)), today),
    )
    row = await cursor.fetchone()
    return _as_int(_row_get(row, "total", 0))


async def record_dm_delivery_failure(
    db: Any,
    *,
    user_id: int,
    error: str,
    disable: bool,
) -> None:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"""
        UPDATE {PREFERENCES_TABLE}
        SET failure_count = failure_count + 1,
            last_error = ?,
            enabled = CASE WHEN ? = 1 THEN 0 ELSE enabled END,
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            _clean_text(error, limit=500),
            int(disable),
            datetime.now(timezone.utc).isoformat(),
            str(int(user_id)),
        ),
    )
    await conn.commit()


async def clear_dm_delivery_failures(db: Any, *, user_id: int) -> None:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"""
        UPDATE {PREFERENCES_TABLE}
        SET failure_count = 0, last_error = '', updated_at = ?
        WHERE user_id = ?
        """,
        (datetime.now(timezone.utc).isoformat(), str(int(user_id))),
    )
    await conn.commit()


def normalize_categories(values: Iterable[str] | str | None) -> tuple[str, ...]:
    raw = _split_values(values)
    expanded: list[str] = []
    for value in raw:
        key = value.replace("-", "_").replace(" ", "_")
        aliases = CATEGORY_ALIASES.get(key)
        if aliases:
            expanded.extend(aliases)
        else:
            expanded.append(key)
    return _dedupe(expanded)


def daily_cap_label(value: int) -> str:
    return "unlimited" if int(value) == UNLIMITED_MAX_ALERTS_PER_DAY else f"{int(value)}/day"


def card_title(card: Any) -> str:
    embed = getattr(card, "embed", None)
    embed_title = str(getattr(embed, "title", "") or "") if embed is not None else ""
    title = str(getattr(card, "label", "") or embed_title or "Walmart deal")
    for prefix in ("🚨 ", "🔥 ", "💎 ", "✅ "):
        title = title.replace(prefix, "")
    if " OFF • " in title:
        title = title.split(" OFF • ", 1)[1]
    return " ".join(title.split())


def card_search_text(card: Any) -> str:
    parts = [card_title(card), str(getattr(card, "url", "") or "")]
    attrs = getattr(card, "variant_attributes", None)
    if isinstance(attrs, dict):
        for key in (
            "brand",
            "manufacturer",
            "category",
            "dealBadges",
            "apiDealBadges",
            "condition",
            "apiCondition",
        ):
            value = attrs.get(key)
            if value not in (None, ""):
                parts.append(str(value))
    embed = getattr(card, "embed", None)
    if embed is not None:
        parts.append(str(getattr(embed, "description", "") or ""))
        for field in getattr(embed, "fields", []) or []:
            parts.append(str(getattr(field, "name", "") or ""))
            parts.append(str(getattr(field, "value", "") or ""))
    return " ".join(parts).lower()


def walmart_cash_cents(card: Any) -> int:
    attrs = getattr(card, "variant_attributes", None)
    attrs = attrs if isinstance(attrs, dict) else {}
    proof = str(attrs.get("walmartCashApiProof") or "").strip().lower()
    if proof not in {"yes", "true", "1", "verified", "api"}:
        return 0
    for key in (
        "walmartCashAmount",
        "walmartCashValue",
        "walmartCash",
        "apiWalmartCashAmount",
    ):
        cents = _money_to_cents(attrs.get(key))
        if cents is not None and 0 < cents <= 100_000_00:
            return cents
    return 0


def _preference_from_row(row: Any) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=_as_int(_row_get(row, "user_id", 0)),
        enabled=bool(_as_int(_row_get(row, "enabled", 1))),
        min_discount=_as_int(_row_get(row, "min_discount", 2)) or DEFAULT_MIN_DISCOUNT,
        max_price_cents=_optional_int(_row_get(row, "max_price_cents", 3)),
        categories=_json_tuple(_row_get(row, "categories_json", 4)),
        max_alerts_per_day=_daily_cap_from_row(
            _row_get(row, "max_alerts_per_day", 5)
        ),
        failure_count=_as_int(_row_get(row, "failure_count", 6)),
        last_error=str(_row_get(row, "last_error", 7) or ""),
    )


def _normalize_daily_cap(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_MAX_ALERTS_PER_DAY
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_ALERTS_PER_DAY
    if cap == UNLIMITED_MAX_ALERTS_PER_DAY:
        return UNLIMITED_MAX_ALERTS_PER_DAY
    return max(1, min(100, cap))


def _daily_cap_from_row(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_MAX_ALERTS_PER_DAY
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_ALERTS_PER_DAY


def _split_values(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        source = values.replace("\n", ",").split(",")
    else:
        source = list(values)
    cleaned: list[str] = []
    for value in source:
        text = " ".join(str(value or "").strip().lower().split())
        if text:
            cleaned.append(text)
    return cleaned


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return tuple(output)


def _json_tuple(value: Any) -> tuple[str, ...]:
    try:
        loaded = json.loads(str(value or "[]"))
    except Exception:
        return ()
    if not isinstance(loaded, list):
        return ()
    return _dedupe(str(item) for item in loaded)


def _money_to_cents(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.replace("$", "").replace(",", "").strip()
    else:
        text = str(value)
    try:
        amount = float(text)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return int(round(amount * 100))


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
