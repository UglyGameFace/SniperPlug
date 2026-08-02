from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable

from sniperplug.services.opportunity_watchlist import category_for_title


PREFERENCES_TABLE = "user_dm_deal_alert_preferences"
RECEIPTS_TABLE = "user_dm_deal_alert_receipts"
VALID_MODES = {"smart", "all", "custom"}
DEFAULT_MODE = "smart"
DEFAULT_MIN_DISCOUNT = 35
DEFAULT_MIN_SCORE = 78
DEFAULT_MAX_ALERTS_PER_DAY = 25
MAX_FILTER_TERMS = 12

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "tech": (
        "brand_direct_electronics",
        "apple",
        "gpus",
        "cpus",
        "ram",
        "ssds",
        "mobile_accessories",
        "smart_home",
    ),
    "gaming": ("brand_direct_electronics", "gpus", "cpus", "ram", "ssds", "toys_collectibles"),
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
    mode: str = DEFAULT_MODE
    min_discount: int = DEFAULT_MIN_DISCOUNT
    max_price_cents: int | None = None
    min_score: int = DEFAULT_MIN_SCORE
    min_savings_cents: int = 0
    categories: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    walmart_cash_only: bool = False
    max_alerts_per_day: int = DEFAULT_MAX_ALERTS_PER_DAY
    failure_count: int = 0
    last_error: str = ""

    def normalized(self) -> "DmDealAlertPreference":
        mode = str(self.mode or DEFAULT_MODE).strip().lower()
        if mode not in VALID_MODES:
            mode = DEFAULT_MODE
        return replace(
            self,
            user_id=int(self.user_id),
            mode=mode,
            min_discount=max(0, min(95, int(self.min_discount))),
            max_price_cents=(
                max(1, int(self.max_price_cents))
                if self.max_price_cents not in (None, 0)
                else None
            ),
            min_score=max(0, min(250, int(self.min_score))),
            min_savings_cents=max(0, int(self.min_savings_cents)),
            categories=normalize_categories(self.categories),
            keywords=normalize_terms(self.keywords),
            exclude_keywords=normalize_terms(self.exclude_keywords),
            max_alerts_per_day=max(1, min(100, int(self.max_alerts_per_day))),
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
        keywords = ", ".join(normalized.keywords) if normalized.keywords else "none"
        excluded = ", ".join(normalized.exclude_keywords) if normalized.exclude_keywords else "none"
        return (
            f"Enabled: **{'yes' if normalized.enabled else 'no'}**",
            f"Mode: **{normalized.mode}**",
            f"Minimum markdown: **{normalized.min_discount}%**",
            f"Minimum Sniper score: **{normalized.min_score}/250**",
            f"Minimum dollar savings: **${normalized.min_savings_cents / 100:,.2f}**",
            f"Maximum price: **{price}**",
            f"Categories: **{categories}**",
            f"Must contain: **{keywords}**",
            f"Exclude: **{excluded}**",
            f"Walmart Cash only: **{'yes' if normalized.walmart_cash_only else 'no'}**",
            f"Daily safety cap: **{normalized.max_alerts_per_day}**",
        )


@dataclass(frozen=True)
class DmDealMatchDecision:
    matched: bool
    reason: str
    category_key: str = "uncategorized"
    required_discount: int = 0
    savings_cents: int = 0
    walmart_cash_cents: int = 0


async def ensure_dm_deal_alert_tables(db: Any) -> None:
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
    await conn.commit()


async def get_dm_deal_alert_preference(db: Any, user_id: int) -> DmDealAlertPreference:
    await ensure_dm_deal_alert_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT user_id, enabled, mode, min_discount, max_price_cents,
               min_score, min_savings_cents, categories_json, keywords_json,
               exclude_keywords_json, walmart_cash_only, max_alerts_per_day,
               failure_count, last_error
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
        SELECT user_id, enabled, mode, min_discount, max_price_cents,
               min_score, min_savings_cents, categories_json, keywords_json,
               exclude_keywords_json, walmart_cash_only, max_alerts_per_day,
               failure_count, last_error
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
            normalized.mode,
            normalized.min_discount,
            normalized.max_price_cents,
            normalized.min_score,
            normalized.min_savings_cents,
            json.dumps(list(normalized.categories)),
            json.dumps(list(normalized.keywords)),
            json.dumps(list(normalized.exclude_keywords)),
            int(normalized.walmart_cash_only),
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


def match_dm_deal(preference: DmDealAlertPreference, card: Any) -> DmDealMatchDecision:
    pref = preference.normalized()
    if not pref.enabled:
        return DmDealMatchDecision(False, "DM alerts are disabled")

    title = card_title(card)
    search_text = card_search_text(card)
    category = category_for_title(title)
    category_key = category.key if category is not None else "uncategorized"
    attrs = getattr(card, "variant_attributes", None)
    attrs = attrs if isinstance(attrs, dict) else {}

    current_cents = _money_to_cents(
        getattr(card, "api_current_price", None)
        or getattr(card, "current_price", None)
    )
    reference_cents = _money_to_cents(
        getattr(card, "api_reference_price", None)
        or getattr(card, "typical_price", None)
    )
    discount = _as_float(
        getattr(card, "api_discount_percent", None)
        or getattr(card, "discount", None)
    )
    score = _as_int(getattr(card, "score", 0))
    savings_cents = max(0, (reference_cents or 0) - (current_cents or 0))
    cash_cents = walmart_cash_cents(card)

    if current_cents is None or reference_cents is None or discount is None:
        return DmDealMatchDecision(False, "exact current/was price proof is incomplete", category_key)
    if current_cents <= 0 or reference_cents <= current_cents:
        return DmDealMatchDecision(False, "exact markdown is not positive", category_key)
    if pref.max_price_cents is not None and current_cents > pref.max_price_cents:
        return DmDealMatchDecision(False, "price is above your maximum", category_key)
    if pref.walmart_cash_only and cash_cents <= 0:
        return DmDealMatchDecision(False, "Walmart Cash proof is required", category_key)
    if pref.categories and category_key not in pref.categories:
        if not (cash_cents > 0 and "walmart_cash" in pref.categories):
            return DmDealMatchDecision(False, "category is not selected", category_key)
    if pref.keywords and not any(term in search_text for term in pref.keywords):
        return DmDealMatchDecision(False, "none of your required keywords matched", category_key)
    if pref.exclude_keywords and any(term in search_text for term in pref.exclude_keywords):
        return DmDealMatchDecision(False, "an excluded keyword matched", category_key)

    required_discount = pref.min_discount
    required_score = pref.min_score
    required_savings = pref.min_savings_cents

    if pref.mode == "smart":
        smart_discount, smart_savings = smart_requirements(current_cents)
        required_discount = max(20, min(pref.min_discount, smart_discount))
        required_score = max(70, min(pref.min_score, 110))
        required_savings = max(pref.min_savings_cents, smart_savings)
        # Strict API-proven Walmart Cash is useful extra value, but it may only
        # soften a real markdown requirement. It can never create a deal alone.
        if cash_cents > 0 and discount >= 20:
            required_discount = max(20, required_discount - 5)

    if discount < required_discount:
        return DmDealMatchDecision(
            False,
            f"{discount:.0f}% is below the required {required_discount}%",
            category_key,
            required_discount,
            savings_cents,
            cash_cents,
        )
    if score < required_score and discount < 70:
        return DmDealMatchDecision(
            False,
            f"score {score} is below the required {required_score}",
            category_key,
            required_discount,
            savings_cents,
            cash_cents,
        )
    if savings_cents < required_savings and discount < 70:
        return DmDealMatchDecision(
            False,
            "dollar savings are below your smart minimum",
            category_key,
            required_discount,
            savings_cents,
            cash_cents,
        )

    reason = (
        f"{discount:.0f}% exact markdown • saves ${savings_cents / 100:,.2f} • "
        f"score {score}/250 • category {category_key}"
    )
    if cash_cents > 0:
        reason += f" • ${cash_cents / 100:,.2f} Walmart Cash"
    return DmDealMatchDecision(
        True,
        reason,
        category_key,
        required_discount,
        savings_cents,
        cash_cents,
    )


def smart_requirements(current_cents: int) -> tuple[int, int]:
    dollars = max(0.0, current_cents / 100)
    if dollars <= 10:
        return 50, 300
    if dollars <= 50:
        return 40, 800
    if dollars <= 200:
        return 35, 2000
    if dollars <= 500:
        return 30, 4000
    return 25, 7500


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


def normalize_terms(values: Iterable[str] | str | None) -> tuple[str, ...]:
    return _dedupe(_split_values(values))[:MAX_FILTER_TERMS]


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
        mode=str(_row_get(row, "mode", 2) or DEFAULT_MODE),
        min_discount=_as_int(_row_get(row, "min_discount", 3)) or DEFAULT_MIN_DISCOUNT,
        max_price_cents=_optional_int(_row_get(row, "max_price_cents", 4)),
        min_score=_as_int(_row_get(row, "min_score", 5)) or DEFAULT_MIN_SCORE,
        min_savings_cents=_as_int(_row_get(row, "min_savings_cents", 6)),
        categories=_json_tuple(_row_get(row, "categories_json", 7)),
        keywords=_json_tuple(_row_get(row, "keywords_json", 8)),
        exclude_keywords=_json_tuple(_row_get(row, "exclude_keywords_json", 9)),
        walmart_cash_only=bool(_as_int(_row_get(row, "walmart_cash_only", 10))),
        max_alerts_per_day=(
            _as_int(_row_get(row, "max_alerts_per_day", 11))
            or DEFAULT_MAX_ALERTS_PER_DAY
        ),
        failure_count=_as_int(_row_get(row, "failure_count", 12)),
        last_error=str(_row_get(row, "last_error", 13) or ""),
    )


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
