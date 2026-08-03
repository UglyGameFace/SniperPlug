from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable

from sniperplug.target_watcher.parser import (
    TargetOffer,
    TargetProductSeed,
    canonical_target_product_url,
    normalize_tcin,
)


SITEMAP_TABLE = "target_pdp_sitemap_sources"
PRODUCT_TABLE = "target_redsky_products"
HISTORY_TABLE = "target_redsky_offer_history"
HEALTH_TABLE = "target_redsky_watcher_health"


@dataclass(frozen=True)
class TargetSitemapSource:
    url: str
    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True)
class TargetCatalogProduct:
    product_key: str
    tcin: str
    store_id: str
    zip_code: str
    title: str
    product_url: str
    image_url: str
    previous_current_price: float | None = None
    previous_reference_price: float | None = None
    previous_reference_source: str = ""
    previous_available: bool | None = None
    previous_promotion_text: str = ""


@dataclass(frozen=True)
class TargetOfferDecision:
    product_key: str
    event_key: str = ""
    event_type: str = ""
    should_publish: bool = False
    previous_price: float | None = None
    current_price: float | None = None
    reference_price: float | None = None
    reference_source: str = ""
    discount_percent: float = 0.0
    next_check_at: str = ""


async def ensure_target_watcher_tables(db: Any) -> None:
    if getattr(db, "_target_watcher_tables_ready", False):
        return
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SITEMAP_TABLE} (
            url TEXT PRIMARY KEY,
            etag TEXT NOT NULL DEFAULT '',
            last_modified TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_checked_at TEXT,
            next_check_at TEXT NOT NULL,
            last_success_at TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{SITEMAP_TABLE}_due "
        f"ON {SITEMAP_TABLE} (next_check_at, last_success_at)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PRODUCT_TABLE} (
            product_key TEXT PRIMARY KEY,
            tcin TEXT NOT NULL,
            store_id TEXT NOT NULL,
            zip_code TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            product_url TEXT NOT NULL,
            image_url TEXT NOT NULL DEFAULT '',
            seller_name TEXT NOT NULL DEFAULT 'Target',
            variant_label TEXT NOT NULL DEFAULT '',
            variant_json TEXT NOT NULL DEFAULT '{{}}',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            offer_checked_at TEXT,
            offer_next_check_at TEXT NOT NULL,
            current_price_cents INTEGER,
            reference_price_cents INTEGER,
            reference_source TEXT NOT NULL DEFAULT '',
            available INTEGER,
            shipping_available INTEGER,
            pickup_available INTEGER,
            can_add_to_cart INTEGER,
            stock_status TEXT NOT NULL DEFAULT '',
            promotion_text TEXT NOT NULL DEFAULT '',
            last_event_key TEXT NOT NULL DEFAULT '',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            UNIQUE(tcin, store_id, zip_code)
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{PRODUCT_TABLE}_due "
        f"ON {PRODUCT_TABLE} (offer_next_check_at, reference_price_cents, current_price_cents)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{PRODUCT_TABLE}_identity "
        f"ON {PRODUCT_TABLE} (tcin, store_id, zip_code)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
            observation_key TEXT PRIMARY KEY,
            product_key TEXT NOT NULL,
            current_price_cents INTEGER NOT NULL,
            reference_price_cents INTEGER,
            reference_source TEXT NOT NULL DEFAULT '',
            available INTEGER,
            shipping_available INTEGER,
            pickup_available INTEGER,
            can_add_to_cart INTEGER,
            promotion_text TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL,
            FOREIGN KEY(product_key) REFERENCES {PRODUCT_TABLE}(product_key) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_product "
        f"ON {HISTORY_TABLE} (product_key, observed_at DESC)"
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
    await conn.commit()
    try:
        setattr(db, "_target_watcher_tables_ready", True)
    except Exception:
        pass


async def upsert_sitemap_sources(
    db: Any,
    urls: Iterable[str],
    *,
    now: datetime | None = None,
) -> int:
    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    unique = tuple(
        dict.fromkeys(str(url).strip() for url in urls if str(url).strip())
    )
    for url in unique:
        await conn.execute(
            f"""
            INSERT INTO {SITEMAP_TABLE} (
                url, first_seen_at, last_seen_at, next_check_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (url, now_iso, now_iso, now_iso),
        )
    await conn.commit()
    return len(unique)


async def claim_due_sitemap_sources(
    db: Any,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[TargetSitemapSource]:
    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT url, etag, last_modified
        FROM {SITEMAP_TABLE}
        WHERE next_check_at <= ?
        ORDER BY COALESCE(last_success_at, '') ASC, last_seen_at DESC
        LIMIT ?
        """,
        (_utc(now).isoformat(), max(1, int(limit))),
    )
    return [
        TargetSitemapSource(
            url=str(_row_get(row, "url", 0) or ""),
            etag=str(_row_get(row, "etag", 1) or ""),
            last_modified=str(_row_get(row, "last_modified", 2) or ""),
        )
        for row in await cursor.fetchall()
        if str(_row_get(row, "url", 0) or "")
    ]


async def complete_sitemap_source(
    db: Any,
    *,
    url: str,
    etag: str,
    last_modified: str,
    refresh_minutes: int,
    error: str = "",
    now: datetime | None = None,
) -> None:
    conn = db.require_conn()
    now_dt = _utc(now)
    next_at = now_dt + timedelta(minutes=max(5, int(refresh_minutes)))
    if error:
        await conn.execute(
            f"""
            UPDATE {SITEMAP_TABLE}
            SET last_checked_at = ?, next_check_at = ?,
                consecutive_failures = consecutive_failures + 1,
                last_error = ?
            WHERE url = ?
            """,
            (now_dt.isoformat(), next_at.isoformat(), _compact(error, 800), url),
        )
    else:
        await conn.execute(
            f"""
            UPDATE {SITEMAP_TABLE}
            SET etag = ?, last_modified = ?, last_checked_at = ?,
                next_check_at = ?, last_success_at = ?,
                consecutive_failures = 0, last_error = ''
            WHERE url = ?
            """,
            (
                etag,
                last_modified,
                now_dt.isoformat(),
                next_at.isoformat(),
                now_dt.isoformat(),
                url,
            ),
        )
    await conn.commit()


async def upsert_product_seeds(
    db: Any,
    seeds: Iterable[TargetProductSeed],
    *,
    store_id: str,
    zip_code: str,
    now: datetime | None = None,
) -> int:
    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    unique: dict[str, TargetProductSeed] = {}
    for seed in seeds:
        tcin = normalize_tcin(seed.tcin)
        if tcin:
            unique[tcin] = TargetProductSeed(
                tcin=tcin,
                product_url=seed.product_url or canonical_target_product_url(tcin),
            )
    if not unique:
        return 0

    records = [
        (
            target_product_key(tcin, store_id=store_id, zip_code=zip_code),
            tcin,
            str(store_id),
            str(zip_code),
            seed.product_url,
            now_iso,
            now_iso,
            now_iso,
        )
        for tcin, seed in unique.items()
    ]
    existing: set[str] = set()
    batch_size = 100
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        keys = [record[0] for record in batch]
        placeholders = ",".join("?" for _ in keys)
        cursor = await conn.execute(
            f"SELECT product_key FROM {PRODUCT_TABLE} WHERE product_key IN ({placeholders})",
            tuple(keys),
        )
        existing.update(
            str(_row_get(row, "product_key", 0) or "")
            for row in await cursor.fetchall()
        )
        row_placeholders = ",".join("(?, ?, ?, ?, ?, ?, ?, ?)" for _ in batch)
        params: list[Any] = []
        for record in batch:
            params.extend(record)
        await conn.execute(
            f"""
            INSERT INTO {PRODUCT_TABLE} (
                product_key, tcin, store_id, zip_code, product_url,
                first_seen_at, last_seen_at, offer_next_check_at
            ) VALUES {row_placeholders}
            ON CONFLICT(product_key) DO UPDATE SET
                product_url = excluded.product_url,
                last_seen_at = excluded.last_seen_at
            """,
            tuple(params),
        )
    await conn.commit()
    return sum(1 for product_key, *_ in records if product_key not in existing)


async def seed_target_tcins(
    db: Any,
    tcins: Iterable[str],
    *,
    store_id: str,
    zip_code: str,
    now: datetime | None = None,
) -> int:
    seeds = [
        TargetProductSeed(tcin=tcin, product_url=canonical_target_product_url(tcin))
        for tcin in (normalize_tcin(value) for value in tcins)
        if tcin
    ]
    return await upsert_product_seeds(
        db,
        seeds,
        store_id=store_id,
        zip_code=zip_code,
        now=now,
    )


async def claim_products_for_offer_poll(
    db: Any,
    *,
    limit: int,
    big_ticket_min_reference_price: float,
    price_error_min_discount_percent: int,
    now: datetime | None = None,
) -> list[TargetCatalogProduct]:
    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    big_ticket_cents = _to_cents(big_ticket_min_reference_price)
    discount_floor = max(1, int(price_error_min_discount_percent))
    cursor = await conn.execute(
        f"""
        SELECT product_key, tcin, store_id, zip_code, title, product_url,
               image_url, current_price_cents, reference_price_cents,
               reference_source, available, promotion_text
        FROM {PRODUCT_TABLE}
        WHERE offer_next_check_at <= ?
        ORDER BY
            CASE
                WHEN reference_price_cents >= ?
                 AND current_price_cents > 0
                 AND reference_price_cents > current_price_cents
                 AND ((reference_price_cents - current_price_cents) * 100)
                     >= (reference_price_cents * ?)
                THEN 0
                WHEN current_price_cents IS NULL THEN 1
                WHEN reference_price_cents > current_price_cents THEN 2
                ELSE 3
            END,
            COALESCE(offer_checked_at, '') ASC,
            last_seen_at DESC
        LIMIT ?
        """,
        (
            _utc(now).isoformat(),
            big_ticket_cents,
            discount_floor,
            max(1, int(limit)),
        ),
    )
    return [_catalog_product(row) for row in await cursor.fetchall()]


async def record_exact_offer(
    db: Any,
    *,
    product: TargetCatalogProduct,
    offer: TargetOffer,
    min_event_discount_percent: int,
    normal_interval_minutes: int,
    markdown_interval_seconds: int,
    big_ticket_min_reference_price: float,
    price_error_min_discount_percent: int,
    big_ticket_interval_seconds: int,
    now: datetime | None = None,
) -> TargetOfferDecision:
    await ensure_target_watcher_tables(db)
    if offer.tcin != product.tcin:
        raise ValueError("Target exact offer identity did not match the claimed TCIN")

    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT current_price_cents, reference_price_cents, reference_source,
               available, promotion_text, last_event_key
        FROM {PRODUCT_TABLE}
        WHERE product_key = ?
        """,
        (product.product_key,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("Target claimed product disappeared before offer storage")

    current_cents = _to_cents(offer.current_price)
    regular_cents = (
        _to_cents(offer.regular_price) if offer.regular_price is not None else None
    )
    previous_cents = _int_or_none(_row_get(row, "current_price_cents", 0))
    previous_reference_cents = _int_or_none(
        _row_get(row, "reference_price_cents", 1)
    )
    previous_reference_source = str(
        _row_get(row, "reference_source", 2) or ""
    )
    previous_available = _db_bool(_row_get(row, "available", 3))
    previous_promotion = str(_row_get(row, "promotion_text", 4) or "")
    last_event_key = str(_row_get(row, "last_event_key", 5) or "")

    if regular_cents is not None and regular_cents > current_cents:
        reference_cents = regular_cents
        reference_source = "target.redsky.product.price.reg_retail"
    elif previous_cents is not None and previous_cents > current_cents:
        reference_cents = previous_cents
        reference_source = "sniperplug.target.exact_price_history.previous_price"
    elif previous_reference_cents is not None and previous_reference_cents > current_cents:
        reference_cents = previous_reference_cents
        reference_source = (
            previous_reference_source
            or "sniperplug.target.exact_price_history.reference_price"
        )
    else:
        reference_cents = None
        reference_source = ""

    available = _offer_available(offer)
    active_markdown = bool(
        reference_cents is not None
        and reference_cents > current_cents
        and available is True
        and offer.can_add_to_cart is not False
    )
    discount = (
        (reference_cents - current_cents) / reference_cents * 100.0
        if active_markdown and reference_cents
        else 0.0
    )
    event_type = ""
    if active_markdown and discount >= max(1, int(min_event_discount_percent)):
        if previous_cents is None and regular_cents is not None:
            event_type = "regular_price_markdown"
        elif previous_cents is not None and current_cents < previous_cents:
            event_type = "price_drop"
        elif previous_available is False and available is True:
            event_type = "back_in_stock"
        elif (
            previous_promotion
            and offer.promotion_text
            and previous_promotion != offer.promotion_text
        ):
            event_type = "promotion_change"

    event_key = ""
    if event_type:
        event_key = _event_key(
            product_key=product.product_key,
            event_type=event_type,
            current_cents=current_cents,
            reference_cents=reference_cents,
            available=available,
            promotion_text=offer.promotion_text,
        )
        if event_key == last_event_key:
            event_key = ""

    if (
        reference_cents is not None
        and reference_cents >= _to_cents(big_ticket_min_reference_price)
        and discount >= max(1, int(price_error_min_discount_percent))
    ):
        next_at = now_dt + timedelta(
            seconds=max(30, int(big_ticket_interval_seconds))
        )
    elif active_markdown:
        next_at = now_dt + timedelta(
            seconds=max(30, int(markdown_interval_seconds))
        )
    else:
        next_at = now_dt + timedelta(
            minutes=max(5, int(normal_interval_minutes))
        )

    await conn.execute(
        f"""
        UPDATE {PRODUCT_TABLE}
        SET title = ?, product_url = ?, image_url = ?, seller_name = ?,
            variant_label = ?, variant_json = ?,
            offer_checked_at = ?, offer_next_check_at = ?,
            current_price_cents = ?, reference_price_cents = ?,
            reference_source = ?, available = ?,
            shipping_available = ?, pickup_available = ?,
            can_add_to_cart = ?, stock_status = ?, promotion_text = ?,
            last_event_key = CASE WHEN ? <> '' THEN ? ELSE last_event_key END,
            consecutive_failures = 0, last_error = ''
        WHERE product_key = ?
        """,
        (
            _compact(offer.title, 500),
            offer.product_url,
            offer.image_url,
            _compact(offer.seller_name, 200),
            _compact(offer.variant_label, 300),
            _json_text(offer.variant_attributes),
            now_iso,
            next_at.isoformat(),
            current_cents,
            reference_cents,
            reference_source,
            _bool_db(available),
            _bool_db(offer.shipping_available),
            _bool_db(offer.pickup_available),
            _bool_db(offer.can_add_to_cart),
            _compact(offer.stock_status, 500),
            _compact(offer.promotion_text, 800),
            event_key,
            event_key,
            product.product_key,
        ),
    )
    observation_key = _observation_key(
        product.product_key,
        current_cents,
        reference_cents,
        available,
        offer.promotion_text,
        now_iso,
    )
    await conn.execute(
        f"""
        INSERT INTO {HISTORY_TABLE} (
            observation_key, product_key, current_price_cents,
            reference_price_cents, reference_source, available,
            shipping_available, pickup_available, can_add_to_cart,
            promotion_text, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_key) DO NOTHING
        """,
        (
            observation_key,
            product.product_key,
            current_cents,
            reference_cents,
            reference_source,
            _bool_db(available),
            _bool_db(offer.shipping_available),
            _bool_db(offer.pickup_available),
            _bool_db(offer.can_add_to_cart),
            _compact(offer.promotion_text, 800),
            now_iso,
        ),
    )
    await conn.commit()
    return TargetOfferDecision(
        product_key=product.product_key,
        event_key=event_key,
        event_type=event_type if event_key else "",
        should_publish=bool(event_key),
        previous_price=_from_cents(previous_cents),
        current_price=_from_cents(current_cents),
        reference_price=_from_cents(reference_cents),
        reference_source=reference_source,
        discount_percent=round(discount, 2),
        next_check_at=next_at.isoformat(),
    )


async def store_offer_failure(
    db: Any,
    *,
    product_keys: Iterable[str],
    error: str,
    now: datetime | None = None,
) -> None:
    conn = db.require_conn()
    now_dt = _utc(now)
    next_at = now_dt + timedelta(minutes=10)
    for product_key in dict.fromkeys(str(value) for value in product_keys if value):
        await conn.execute(
            f"""
            UPDATE {PRODUCT_TABLE}
            SET offer_checked_at = ?, offer_next_check_at = ?,
                consecutive_failures = consecutive_failures + 1,
                last_error = ?
            WHERE product_key = ?
            """,
            (
                now_dt.isoformat(),
                next_at.isoformat(),
                _compact(error, 800),
                product_key,
            ),
        )
    await conn.commit()


async def set_health_value(
    db: Any,
    key: str,
    value: str,
    *,
    now: datetime | None = None,
) -> None:
    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"""
        INSERT INTO {HEALTH_TABLE} (state_key, state_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(state_key) DO UPDATE SET
            state_value = excluded.state_value,
            updated_at = excluded.updated_at
        """,
        (_compact(key, 100), _compact(value, 2000), _utc(now).isoformat()),
    )
    await conn.commit()


async def target_watcher_counts(db: Any) -> dict[str, int]:
    await ensure_target_watcher_tables(db)
    conn = db.require_conn()
    queries = {
        "sitemap_sources": f"SELECT COUNT(*) FROM {SITEMAP_TABLE}",
        "products": f"SELECT COUNT(*) FROM {PRODUCT_TABLE}",
        "priced_products": (
            f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE current_price_cents > 0"
        ),
        "active_markdowns": (
            f"SELECT COUNT(*) FROM {PRODUCT_TABLE} "
            "WHERE current_price_cents > 0 AND reference_price_cents > current_price_cents"
        ),
        "failures": (
            f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE consecutive_failures > 0"
        ),
    }
    result: dict[str, int] = {}
    for key, sql in queries.items():
        cursor = await conn.execute(sql)
        row = await cursor.fetchone()
        result[key] = int(_row_get(row, "COUNT(*)", 0) or 0)
    return result


def target_product_key(tcin: str, *, store_id: str, zip_code: str) -> str:
    clean = normalize_tcin(tcin)
    if not clean:
        raise ValueError("Target product key requires a numeric TCIN")
    return f"target:{str(store_id).strip()}:{str(zip_code).strip()}:{clean}"


def _catalog_product(row: Any) -> TargetCatalogProduct:
    return TargetCatalogProduct(
        product_key=str(_row_get(row, "product_key", 0) or ""),
        tcin=str(_row_get(row, "tcin", 1) or ""),
        store_id=str(_row_get(row, "store_id", 2) or ""),
        zip_code=str(_row_get(row, "zip_code", 3) or ""),
        title=str(_row_get(row, "title", 4) or ""),
        product_url=str(_row_get(row, "product_url", 5) or ""),
        image_url=str(_row_get(row, "image_url", 6) or ""),
        previous_current_price=_from_cents(
            _int_or_none(_row_get(row, "current_price_cents", 7))
        ),
        previous_reference_price=_from_cents(
            _int_or_none(_row_get(row, "reference_price_cents", 8))
        ),
        previous_reference_source=str(
            _row_get(row, "reference_source", 9) or ""
        ),
        previous_available=_db_bool(_row_get(row, "available", 10)),
        previous_promotion_text=str(_row_get(row, "promotion_text", 11) or ""),
    )


def _offer_available(offer: TargetOffer) -> bool | None:
    values = (
        offer.can_add_to_cart,
        offer.shipping_available,
        offer.pickup_available,
    )
    if any(value is True for value in values):
        return True
    known = [value for value in values if value is not None]
    if known and all(value is False for value in known):
        return False
    return None


def _event_key(
    *,
    product_key: str,
    event_type: str,
    current_cents: int,
    reference_cents: int | None,
    available: bool | None,
    promotion_text: str,
) -> str:
    material = "|".join(
        (
            product_key,
            event_type,
            str(current_cents),
            str(reference_cents or 0),
            str(available),
            _compact(promotion_text, 200),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"target-event:v1:{digest}"


def _observation_key(
    product_key: str,
    current_cents: int,
    reference_cents: int | None,
    available: bool | None,
    promotion_text: str,
    observed_at: str,
) -> str:
    material = "|".join(
        (
            product_key,
            str(current_cents),
            str(reference_cents or 0),
            str(available),
            _compact(promotion_text, 200),
            observed_at,
        )
    )
    return "target-observation:v1:" + hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _to_cents(value: float) -> int:
    return int(round(float(value) * 100))


def _from_cents(value: int | None) -> float | None:
    return round(value / 100.0, 2) if value is not None else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bool_db(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))


def _db_bool(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


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


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]
