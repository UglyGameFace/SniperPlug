from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Iterable

from sniperplug.target_watcher import storage


LEASE_TABLE = "target_watcher_work_leases"
SITEMAP_WORK_TYPE = "sitemap"
PRODUCT_WORK_TYPE = "product"
DEFAULT_SITEMAP_LEASE_SECONDS = 600
DEFAULT_PRODUCT_LEASE_SECONDS = 300


@dataclass(frozen=True)
class LeasedTargetSitemapSource:
    url: str
    etag: str = ""
    last_modified: str = ""
    claim_token: str = ""


@dataclass(frozen=True)
class LeasedTargetCatalogProduct:
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
    claim_token: str = ""


async def ensure_target_watcher_lease_table(db: Any) -> None:
    if getattr(db, "_target_watcher_lease_table_ready", False):
        return
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {LEASE_TABLE} (
            work_type TEXT NOT NULL,
            work_key TEXT NOT NULL,
            claim_token TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            lease_until TEXT NOT NULL,
            PRIMARY KEY(work_type, work_key)
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{LEASE_TABLE}_expiry "
        f"ON {LEASE_TABLE} (work_type, lease_until)"
    )
    await conn.commit()
    try:
        setattr(db, "_target_watcher_lease_table_ready", True)
    except Exception:
        pass


async def claim_due_sitemap_sources(
    db: Any,
    *,
    limit: int,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_SITEMAP_LEASE_SECONDS,
) -> list[LeasedTargetSitemapSource]:
    await ensure_target_watcher_lease_table(db)
    requested = max(1, int(limit))
    candidates = await storage.claim_due_sitemap_sources(
        db,
        limit=max(requested * 5, requested),
        now=now,
    )
    claimed: list[LeasedTargetSitemapSource] = []
    for source in candidates:
        token = await _try_acquire_lease(
            db,
            work_type=SITEMAP_WORK_TYPE,
            work_key=source.url,
            now=now,
            lease_seconds=lease_seconds,
        )
        if not token:
            continue
        claimed.append(
            LeasedTargetSitemapSource(
                url=source.url,
                etag=source.etag,
                last_modified=source.last_modified,
                claim_token=token,
            )
        )
        if len(claimed) >= requested:
            break
    return claimed


async def complete_sitemap_source(
    db: Any,
    *,
    source: LeasedTargetSitemapSource,
    etag: str,
    last_modified: str,
    refresh_minutes: int,
    error: str = "",
    now: datetime | None = None,
) -> bool:
    if not await _owns_live_lease(
        db,
        work_type=SITEMAP_WORK_TYPE,
        work_key=source.url,
        claim_token=source.claim_token,
        now=now,
    ):
        return False
    await storage.complete_sitemap_source(
        db,
        url=source.url,
        etag=etag,
        last_modified=last_modified,
        refresh_minutes=refresh_minutes,
        error=error,
        now=now,
    )
    await _release_lease(
        db,
        work_type=SITEMAP_WORK_TYPE,
        work_key=source.url,
        claim_token=source.claim_token,
    )
    return True


async def claim_products_for_offer_poll(
    db: Any,
    *,
    limit: int,
    big_ticket_min_reference_price: float,
    price_error_min_discount_percent: int,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_PRODUCT_LEASE_SECONDS,
) -> list[LeasedTargetCatalogProduct]:
    await ensure_target_watcher_lease_table(db)
    requested = max(1, int(limit))
    candidates = await storage.claim_products_for_offer_poll(
        db,
        limit=max(requested * 5, requested),
        big_ticket_min_reference_price=big_ticket_min_reference_price,
        price_error_min_discount_percent=price_error_min_discount_percent,
        now=now,
    )
    claimed: list[LeasedTargetCatalogProduct] = []
    for product in candidates:
        token = await _try_acquire_lease(
            db,
            work_type=PRODUCT_WORK_TYPE,
            work_key=product.product_key,
            now=now,
            lease_seconds=lease_seconds,
        )
        if not token:
            continue
        claimed.append(
            LeasedTargetCatalogProduct(
                product_key=product.product_key,
                tcin=product.tcin,
                store_id=product.store_id,
                zip_code=product.zip_code,
                title=product.title,
                product_url=product.product_url,
                image_url=product.image_url,
                previous_current_price=product.previous_current_price,
                previous_reference_price=product.previous_reference_price,
                previous_reference_source=product.previous_reference_source,
                previous_available=product.previous_available,
                previous_promotion_text=product.previous_promotion_text,
                claim_token=token,
            )
        )
        if len(claimed) >= requested:
            break
    return claimed


async def record_exact_offer(
    db: Any,
    *,
    product: LeasedTargetCatalogProduct,
    offer: Any,
    min_event_discount_percent: int,
    normal_interval_minutes: int,
    markdown_interval_seconds: int,
    big_ticket_min_reference_price: float,
    price_error_min_discount_percent: int,
    big_ticket_interval_seconds: int,
    now: datetime | None = None,
) -> storage.TargetOfferDecision:
    if not await _owns_live_lease(
        db,
        work_type=PRODUCT_WORK_TYPE,
        work_key=product.product_key,
        claim_token=product.claim_token,
        now=now,
    ):
        raise RuntimeError("Target product work lease expired or was reclaimed")
    decision = await storage.record_exact_offer(
        db,
        product=product,
        offer=offer,
        min_event_discount_percent=min_event_discount_percent,
        normal_interval_minutes=normal_interval_minutes,
        markdown_interval_seconds=markdown_interval_seconds,
        big_ticket_min_reference_price=big_ticket_min_reference_price,
        price_error_min_discount_percent=price_error_min_discount_percent,
        big_ticket_interval_seconds=big_ticket_interval_seconds,
        now=now,
    )
    await _release_lease(
        db,
        work_type=PRODUCT_WORK_TYPE,
        work_key=product.product_key,
        claim_token=product.claim_token,
    )
    return decision


async def store_offer_failure(
    db: Any,
    *,
    products: Iterable[LeasedTargetCatalogProduct],
    error: str,
    now: datetime | None = None,
) -> int:
    owned: list[LeasedTargetCatalogProduct] = []
    for product in products:
        if await _owns_live_lease(
            db,
            work_type=PRODUCT_WORK_TYPE,
            work_key=product.product_key,
            claim_token=product.claim_token,
            now=now,
        ):
            owned.append(product)
    if not owned:
        return 0
    await storage.store_offer_failure(
        db,
        product_keys=[product.product_key for product in owned],
        error=error,
        now=now,
    )
    for product in owned:
        await _release_lease(
            db,
            work_type=PRODUCT_WORK_TYPE,
            work_key=product.product_key,
            claim_token=product.claim_token,
        )
    return len(owned)


async def _try_acquire_lease(
    db: Any,
    *,
    work_type: str,
    work_key: str,
    now: datetime | None,
    lease_seconds: int,
) -> str:
    await ensure_target_watcher_lease_table(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    lease_until = (
        now_dt + timedelta(seconds=max(30, int(lease_seconds)))
    ).isoformat()
    claim_token = uuid.uuid4().hex
    await conn.execute(
        f"""
        INSERT INTO {LEASE_TABLE} (
            work_type, work_key, claim_token, claimed_at, lease_until
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(work_type, work_key) DO UPDATE SET
            claim_token = excluded.claim_token,
            claimed_at = excluded.claimed_at,
            lease_until = excluded.lease_until
        WHERE {LEASE_TABLE}.lease_until <= ?
        """,
        (
            work_type,
            work_key,
            claim_token,
            now_iso,
            lease_until,
            now_iso,
        ),
    )
    await conn.commit()
    cursor = await conn.execute(
        f"SELECT claim_token FROM {LEASE_TABLE} "
        "WHERE work_type = ? AND work_key = ?",
        (work_type, work_key),
    )
    row = await cursor.fetchone()
    actual = _row_get(row, "claim_token", 0)
    return claim_token if str(actual or "") == claim_token else ""


async def _owns_live_lease(
    db: Any,
    *,
    work_type: str,
    work_key: str,
    claim_token: str,
    now: datetime | None,
) -> bool:
    if not claim_token:
        return False
    await ensure_target_watcher_lease_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"""
        SELECT 1
        FROM {LEASE_TABLE}
        WHERE work_type = ? AND work_key = ? AND claim_token = ?
          AND lease_until > ?
        LIMIT 1
        """,
        (work_type, work_key, claim_token, _utc(now).isoformat()),
    )
    return await cursor.fetchone() is not None


async def _release_lease(
    db: Any,
    *,
    work_type: str,
    work_key: str,
    claim_token: str,
) -> None:
    if not claim_token:
        return
    conn = db.require_conn()
    await conn.execute(
        f"DELETE FROM {LEASE_TABLE} "
        "WHERE work_type = ? AND work_key = ? AND claim_token = ?",
        (work_type, work_key, claim_token),
    )
    await conn.commit()


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
