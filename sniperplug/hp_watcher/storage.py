from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Iterable

from sniperplug.hp_watcher.parser import HPPriceOffer, ProductPageIdentity


SITEMAP_TABLE = "hp_store_sitemap_sources"
PRODUCT_TABLE = "hp_store_catalog_products"
OFFER_TABLE = "hp_store_offer_state"
HISTORY_TABLE = "hp_store_offer_history"
HEALTH_TABLE = "hp_store_watcher_health"


@dataclass(frozen=True)
class SitemapSource:
    url: str
    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True)
class CatalogProduct:
    product_key: str
    product_url: str
    sku: str
    catalog_entry_id: str
    title: str
    image_url: str
    previous_current_price: float | None = None
    previous_reference_price: float | None = None
    previous_in_stock: bool | None = None


@dataclass(frozen=True)
class OfferDecision:
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


async def ensure_hp_watcher_tables(db: Any) -> None:
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
        f"CREATE INDEX IF NOT EXISTS idx_{SITEMAP_TABLE}_due ON {SITEMAP_TABLE} (next_check_at)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PRODUCT_TABLE} (
            product_key TEXT PRIMARY KEY,
            product_url TEXT NOT NULL UNIQUE,
            sku TEXT NOT NULL DEFAULT '',
            catalog_entry_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            page_checked_at TEXT,
            page_next_check_at TEXT NOT NULL,
            offer_checked_at TEXT,
            offer_next_check_at TEXT NOT NULL,
            current_price_cents INTEGER,
            reference_price_cents INTEGER,
            in_stock INTEGER,
            can_add_to_cart INTEGER,
            promotion_text TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            consecutive_page_failures INTEGER NOT NULL DEFAULT 0,
            consecutive_offer_failures INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{PRODUCT_TABLE}_page_due ON {PRODUCT_TABLE} (page_next_check_at, last_seen_at DESC)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{PRODUCT_TABLE}_offer_due ON {PRODUCT_TABLE} (offer_next_check_at, current_price_cents, last_seen_at DESC)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{PRODUCT_TABLE}_identity ON {PRODUCT_TABLE} (catalog_entry_id, sku)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {OFFER_TABLE} (
            offer_key TEXT PRIMARY KEY,
            product_key TEXT NOT NULL,
            sku TEXT NOT NULL,
            catalog_entry_id TEXT NOT NULL,
            current_price_cents INTEGER NOT NULL,
            reference_price_cents INTEGER,
            lowest_price_cents INTEGER NOT NULL,
            in_stock INTEGER,
            can_add_to_cart INTEGER,
            promotion_text TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_price_change_at TEXT NOT NULL,
            last_event_key TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(product_key) REFERENCES {PRODUCT_TABLE}(product_key) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{OFFER_TABLE}_product ON {OFFER_TABLE} (product_key, catalog_entry_id, sku)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
            observation_key TEXT PRIMARY KEY,
            offer_key TEXT NOT NULL,
            current_price_cents INTEGER NOT NULL,
            reference_price_cents INTEGER,
            in_stock INTEGER,
            can_add_to_cart INTEGER,
            promotion_text TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL,
            FOREIGN KEY(offer_key) REFERENCES {OFFER_TABLE}(offer_key) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_offer ON {HISTORY_TABLE} (offer_key, observed_at DESC)"
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


async def upsert_sitemap_sources(
    db: Any,
    urls: Iterable[str],
    *,
    now: datetime | None = None,
) -> int:
    await ensure_hp_watcher_tables(db)
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    unique = tuple(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))
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
) -> list[SitemapSource]:
    await ensure_hp_watcher_tables(db)
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
        SitemapSource(
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
    next_at = now_dt + timedelta(minutes=max(2, int(refresh_minutes)))
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
            (etag, last_modified, now_dt.isoformat(), next_at.isoformat(), now_dt.isoformat(), url),
        )
    await conn.commit()


async def upsert_product_urls(
    db: Any,
    urls: Iterable[str],
    *,
    now: datetime | None = None,
) -> int:
    await ensure_hp_watcher_tables(db)
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    unique = tuple(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))
    inserted = 0
    for url in unique:
        product_key = hp_product_key(url)
        existing = await conn.execute(
            f"SELECT 1 FROM {PRODUCT_TABLE} WHERE product_key = ?",
            (product_key,),
        )
        is_new = await existing.fetchone() is None
        await conn.execute(
            f"""
            INSERT INTO {PRODUCT_TABLE} (
                product_key, product_url, first_seen_at, last_seen_at,
                page_next_check_at, offer_next_check_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_key) DO UPDATE SET
                product_url = excluded.product_url,
                last_seen_at = excluded.last_seen_at
            """,
            (product_key, url, now_iso, now_iso, now_iso, now_iso),
        )
        inserted += int(is_new)
    await conn.commit()
    return inserted


async def claim_products_for_page_refresh(
    db: Any,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[CatalogProduct]:
    await ensure_hp_watcher_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT product_key, product_url, sku, catalog_entry_id, title, image_url,
               current_price_cents, reference_price_cents, in_stock
        FROM {PRODUCT_TABLE}
        WHERE page_next_check_at <= ?
        ORDER BY
            CASE WHEN sku = '' OR catalog_entry_id = '' THEN 0 ELSE 1 END,
            last_seen_at DESC
        LIMIT ?
        """,
        (_utc(now).isoformat(), max(1, int(limit))),
    )
    return [_catalog_product(row) for row in await cursor.fetchall()]


async def store_product_identity(
    db: Any,
    *,
    product_key: str,
    identity: ProductPageIdentity,
    refresh_hours: int,
    now: datetime | None = None,
) -> None:
    conn = db.require_conn()
    now_dt = _utc(now)
    await conn.execute(
        f"""
        UPDATE {PRODUCT_TABLE}
        SET sku = ?, catalog_entry_id = ?, title = ?, image_url = ?,
            page_checked_at = ?, page_next_check_at = ?,
            offer_next_check_at = CASE
                WHEN offer_checked_at IS NULL THEN ? ELSE offer_next_check_at END,
            consecutive_page_failures = 0, last_error = ''
        WHERE product_key = ?
        """,
        (
            identity.sku,
            identity.catalog_entry_id,
            identity.title,
            identity.image_url,
            now_dt.isoformat(),
            (now_dt + timedelta(hours=max(1, int(refresh_hours)))).isoformat(),
            now_dt.isoformat(),
            product_key,
        ),
    )
    await conn.commit()


async def store_product_page_failure(
    db: Any,
    *,
    product_key: str,
    error: str,
    now: datetime | None = None,
) -> None:
    conn = db.require_conn()
    now_dt = _utc(now)
    await conn.execute(
        f"""
        UPDATE {PRODUCT_TABLE}
        SET page_checked_at = ?, page_next_check_at = ?,
            consecutive_page_failures = consecutive_page_failures + 1,
            last_error = ?
        WHERE product_key = ?
        """,
        (
            now_dt.isoformat(),
            (now_dt + timedelta(minutes=30)).isoformat(),
            _compact(error, 800),
            product_key,
        ),
    )
    await conn.commit()


async def claim_products_for_offer_poll(
    db: Any,
    *,
    limit: int,
    now: datetime | None = None,
) -> list[CatalogProduct]:
    await ensure_hp_watcher_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT product_key, product_url, sku, catalog_entry_id, title, image_url,
               current_price_cents, reference_price_cents, in_stock
        FROM {PRODUCT_TABLE}
        WHERE sku <> ''
          AND catalog_entry_id <> ''
          AND offer_next_check_at <= ?
        ORDER BY
            CASE
                WHEN reference_price_cents IS NOT NULL
                 AND current_price_cents IS NOT NULL
                 AND reference_price_cents > current_price_cents THEN 0
                ELSE 1
            END,
            COALESCE(offer_checked_at, '') ASC,
            last_seen_at DESC
        LIMIT ?
        """,
        (_utc(now).isoformat(), max(1, int(limit))),
    )
    return [_catalog_product(row) for row in await cursor.fetchall()]


async def record_exact_offer(
    db: Any,
    *,
    product: CatalogProduct,
    offer: HPPriceOffer,
    min_event_discount_percent: int,
    normal_interval_minutes: int,
    markdown_interval_seconds: int,
    now: datetime | None = None,
) -> OfferDecision:
    await ensure_hp_watcher_tables(db)
    if offer.product_id != product.catalog_entry_id or offer.sku != product.sku:
        raise ValueError("HP exact offer identity did not match the claimed catalog product")

    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    offer_key = hp_offer_key(product, offer)
    cursor = await conn.execute(
        f"""
        SELECT current_price_cents, reference_price_cents, lowest_price_cents,
               in_stock, can_add_to_cart, promotion_text, last_event_key
        FROM {OFFER_TABLE}
        WHERE offer_key = ?
        """,
        (offer_key,),
    )
    row = await cursor.fetchone()

    current_cents = _to_cents(offer.current_price)
    msrp_cents = _to_cents(offer.msrp_price) if offer.msrp_price is not None else None
    previous_cents = _int_or_none(_row_get(row, "current_price_cents", 0))
    previous_reference_cents = _int_or_none(_row_get(row, "reference_price_cents", 1))
    lowest_cents = _int_or_none(_row_get(row, "lowest_price_cents", 2))
    previous_in_stock = _db_bool(_row_get(row, "in_stock", 3))
    previous_promo = str(_row_get(row, "promotion_text", 5) or "")

    reference_cents: int | None = None
    reference_source = ""
    if msrp_cents is not None and msrp_cents > current_cents:
        reference_cents = msrp_cents
        reference_source = "hp.services.priceData.lPrice.msrp"
    elif previous_cents is not None and previous_cents > current_cents:
        reference_cents = previous_cents
        reference_source = "sniperplug.hp.exact_price_history.previous_price"
    elif previous_reference_cents is not None and previous_reference_cents > current_cents:
        reference_cents = previous_reference_cents
        reference_source = "sniperplug.hp.exact_price_history.reference_price"

    discount = _discount_percent(current_cents, reference_cents)
    active_markdown = bool(
        reference_cents is not None
        and discount >= max(1, int(min_event_discount_percent))
        and offer.in_stock is not False
        and offer.can_add_to_cart is not False
    )
    event_type = ""
    if active_markdown:
        if row is None and reference_source == "hp.services.priceData.lPrice.msrp":
            event_type = "msrp_markdown"
        elif previous_cents is not None and current_cents < previous_cents:
            event_type = "price_drop"
        elif previous_in_stock is False and offer.in_stock is True:
            event_type = "back_in_stock"
        elif offer.promotion_text and offer.promotion_text != previous_promo:
            event_type = "promotion_added"

    event_key = ""
    if event_type:
        event_key = hp_event_key(
            offer_key=offer_key,
            event_type=event_type,
            current_cents=current_cents,
            reference_cents=reference_cents,
            in_stock=offer.in_stock,
            promotion_text=offer.promotion_text,
        )

    next_check = (
        now_dt + timedelta(seconds=max(30, int(markdown_interval_seconds)))
        if active_markdown
        else now_dt + timedelta(minutes=max(5, int(normal_interval_minutes)))
    )
    next_check_iso = next_check.isoformat()
    lowest_next = min(current_cents, lowest_cents) if lowest_cents is not None else current_cents
    price_changed = previous_cents is None or previous_cents != current_cents

    await conn.execute(
        f"""
        INSERT INTO {OFFER_TABLE} (
            offer_key, product_key, sku, catalog_entry_id,
            current_price_cents, reference_price_cents, lowest_price_cents,
            in_stock, can_add_to_cart, promotion_text,
            first_seen_at, last_seen_at, last_price_change_at, last_event_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(offer_key) DO UPDATE SET
            current_price_cents = excluded.current_price_cents,
            reference_price_cents = COALESCE(excluded.reference_price_cents, {OFFER_TABLE}.reference_price_cents),
            lowest_price_cents = MIN({OFFER_TABLE}.lowest_price_cents, excluded.lowest_price_cents),
            in_stock = excluded.in_stock,
            can_add_to_cart = excluded.can_add_to_cart,
            promotion_text = excluded.promotion_text,
            last_seen_at = excluded.last_seen_at,
            last_price_change_at = CASE
                WHEN {OFFER_TABLE}.current_price_cents <> excluded.current_price_cents
                THEN excluded.last_price_change_at
                ELSE {OFFER_TABLE}.last_price_change_at END,
            last_event_key = CASE
                WHEN excluded.last_event_key <> '' THEN excluded.last_event_key
                ELSE {OFFER_TABLE}.last_event_key END
        """,
        (
            offer_key,
            product.product_key,
            product.sku,
            product.catalog_entry_id,
            current_cents,
            reference_cents,
            lowest_next,
            _bool_db(offer.in_stock),
            _bool_db(offer.can_add_to_cart),
            offer.promotion_text,
            now_iso,
            now_iso,
            now_iso,
            event_key,
        ),
    )

    if price_changed or previous_in_stock != offer.in_stock or previous_promo != offer.promotion_text:
        observation_key = hashlib.sha256(
            f"{offer_key}|{current_cents}|{reference_cents}|{offer.in_stock}|{offer.can_add_to_cart}|{offer.promotion_text}".encode("utf-8")
        ).hexdigest()
        await conn.execute(
            f"""
            INSERT INTO {HISTORY_TABLE} (
                observation_key, offer_key, current_price_cents,
                reference_price_cents, in_stock, can_add_to_cart,
                promotion_text, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_key) DO NOTHING
            """,
            (
                observation_key,
                offer_key,
                current_cents,
                reference_cents,
                _bool_db(offer.in_stock),
                _bool_db(offer.can_add_to_cart),
                offer.promotion_text,
                now_iso,
            ),
        )

    await conn.execute(
        f"""
        UPDATE {PRODUCT_TABLE}
        SET offer_checked_at = ?, offer_next_check_at = ?,
            current_price_cents = ?,
            reference_price_cents = COALESCE(?, reference_price_cents),
            in_stock = ?, can_add_to_cart = ?, promotion_text = ?,
            consecutive_offer_failures = 0, last_error = ''
        WHERE product_key = ?
        """,
        (
            now_iso,
            next_check_iso,
            current_cents,
            reference_cents,
            _bool_db(offer.in_stock),
            _bool_db(offer.can_add_to_cart),
            offer.promotion_text,
            product.product_key,
        ),
    )
    await conn.commit()
    return OfferDecision(
        product_key=product.product_key,
        event_key=event_key,
        event_type=event_type,
        should_publish=bool(event_type),
        previous_price=_from_cents(previous_cents),
        current_price=offer.current_price,
        reference_price=_from_cents(reference_cents),
        reference_source=reference_source,
        discount_percent=discount,
        next_check_at=next_check_iso,
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
    for product_key in tuple(dict.fromkeys(product_keys)):
        await conn.execute(
            f"""
            UPDATE {PRODUCT_TABLE}
            SET offer_checked_at = ?, offer_next_check_at = ?,
                consecutive_offer_failures = consecutive_offer_failures + 1,
                last_error = ?
            WHERE product_key = ?
            """,
            (
                now_dt.isoformat(),
                (now_dt + timedelta(minutes=15)).isoformat(),
                _compact(error, 800),
                product_key,
            ),
        )
    await conn.commit()


async def set_health_value(db: Any, key: str, value: Any, *, now: datetime | None = None) -> None:
    await ensure_hp_watcher_tables(db)
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


async def hp_watcher_counts(db: Any) -> dict[str, int]:
    await ensure_hp_watcher_tables(db)
    conn = db.require_conn()
    result: dict[str, int] = {}
    queries = {
        "sitemaps": f"SELECT COUNT(*) FROM {SITEMAP_TABLE}",
        "products": f"SELECT COUNT(*) FROM {PRODUCT_TABLE}",
        "identified_products": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE sku <> '' AND catalog_entry_id <> ''",
        "due_product_pages": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE page_next_check_at <= ?",
        "due_offers": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE sku <> '' AND catalog_entry_id <> '' AND offer_next_check_at <= ?",
        "active_markdowns": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE current_price_cents > 0 AND reference_price_cents > current_price_cents",
    }
    now_iso = datetime.now(timezone.utc).isoformat()
    for key, sql in queries.items():
        cursor = await conn.execute(sql, (now_iso,)) if "?" in sql else await conn.execute(sql)
        row = await cursor.fetchone()
        result[key] = int(_row_get(row, "COUNT(*)", 0) or 0)
    return result


def hp_product_key(product_url: str) -> str:
    return "hp-product:" + hashlib.sha256(str(product_url).strip().encode("utf-8")).hexdigest()


def hp_offer_key(product: CatalogProduct, offer: HPPriceOffer) -> str:
    payload = f"{product.catalog_entry_id}|{product.sku}|{offer.part_number.upper()}|hp.com"
    return "hp-offer:v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hp_event_key(
    *,
    offer_key: str,
    event_type: str,
    current_cents: int,
    reference_cents: int | None,
    in_stock: bool | None,
    promotion_text: str,
) -> str:
    payload = (
        f"{offer_key}|{event_type}|{current_cents}|{reference_cents}|"
        f"{in_stock}|{promotion_text.strip()}"
    )
    return "hp-event:v1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _catalog_product(row: Any) -> CatalogProduct:
    return CatalogProduct(
        product_key=str(_row_get(row, "product_key", 0) or ""),
        product_url=str(_row_get(row, "product_url", 1) or ""),
        sku=str(_row_get(row, "sku", 2) or ""),
        catalog_entry_id=str(_row_get(row, "catalog_entry_id", 3) or ""),
        title=str(_row_get(row, "title", 4) or ""),
        image_url=str(_row_get(row, "image_url", 5) or ""),
        previous_current_price=_from_cents(_int_or_none(_row_get(row, "current_price_cents", 6))),
        previous_reference_price=_from_cents(_int_or_none(_row_get(row, "reference_price_cents", 7))),
        previous_in_stock=_db_bool(_row_get(row, "in_stock", 8)),
    )


def _discount_percent(current_cents: int, reference_cents: int | None) -> float:
    if reference_cents is None or reference_cents <= current_cents:
        return 0.0
    return round((reference_cents - current_cents) / reference_cents * 100.0, 2)


def _to_cents(value: float | None) -> int:
    if value is None or float(value) <= 0:
        raise ValueError("price must be a positive number")
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
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


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
