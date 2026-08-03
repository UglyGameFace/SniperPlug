from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sniperplug.hp_watcher.client import HPStoreClient
from sniperplug.hp_watcher.parser import HPPriceOffer, parse_hp_services_price_response
from sniperplug.hp_watcher.service import (
    HPWatcherService,
    _candidate_for_hp_offer,
    confirm_exact_hp_offer,
    offer_requires_exact_confirmation,
)
from sniperplug.hp_watcher.storage import (
    PRODUCT_TABLE,
    CatalogProduct,
    record_exact_offer,
    set_health_value,
    store_offer_failure,
)
from sniperplug.services.verified_retailer_events import publish_verified_retailer_event


class HPPriceErrorWatcherService(HPWatcherService):
    """HP watcher tuned for expensive, extreme-discount price-error alerts.

    The base watcher still owns sitemap discovery, exact identity parsing, price
    history, fail-closed verification, and health reporting. This subclass only
    replaces offer scheduling/publication so expensive products receive protected
    polling capacity and cheap accessories cannot generate HP public events.
    """

    async def initialize(self) -> None:
        await super().initialize()
        await set_health_value(
            self.db,
            "price_error_policy",
            (
                f"reference_floor={self.settings.big_ticket_min_reference_price:.2f},"
                f"discount_floor={self.settings.price_error_min_discount_percent},"
                f"big_ticket_interval_s={self.settings.big_ticket_offer_interval_seconds}"
            ),
        )

    async def _process_offers(self, client: HPStoreClient) -> tuple[int, int, int, int, int]:
        products = await claim_price_error_offer_batch(
            self.db,
            limit=self.settings.offer_batch_size,
            minimum_reference_price=self.settings.big_ticket_min_reference_price,
        )
        if not products:
            return 0, 0, 0, 0, 0

        expected = {product.catalog_entry_id: product.sku for product in products}
        by_id = {product.catalog_entry_id: product for product in products}
        try:
            document = await client.fetch_price_batch(list(expected))
            offers = await asyncio.to_thread(
                parse_hp_services_price_response,
                document.text,
                expected_products=expected,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - keep every claimed product retryable.
            await store_offer_failure(
                self.db,
                product_keys=[product.product_key for product in products],
                error=f"{type(error).__name__}: {error}",
            )
            return len(products), 0, len(products), 0, 0

        verified = failures = events = confirmations = 0
        returned_ids: set[str] = set()
        big_ticket_keys: list[str] = []
        background_keys: list[str] = []

        for offer in offers:
            product = by_id.get(offer.product_id)
            if product is None:
                continue
            returned_ids.add(offer.product_id)
            try:
                big_ticket = is_big_ticket_product(
                    product,
                    offer,
                    minimum_reference_price=self.settings.big_ticket_min_reference_price,
                )
                price_error = is_big_ticket_price_error(
                    product,
                    offer,
                    minimum_reference_price=self.settings.big_ticket_min_reference_price,
                    minimum_discount_percent=self.settings.price_error_min_discount_percent,
                )

                if price_error and offer_requires_exact_confirmation(
                    product,
                    offer,
                    min_discount=self.settings.price_error_min_discount_percent,
                ):
                    offer = await confirm_exact_hp_offer(client, product, offer)
                    confirmations += 1
                    big_ticket = is_big_ticket_product(
                        product,
                        offer,
                        minimum_reference_price=self.settings.big_ticket_min_reference_price,
                    )
                    price_error = is_big_ticket_price_error(
                        product,
                        offer,
                        minimum_reference_price=self.settings.big_ticket_min_reference_price,
                        minimum_discount_percent=self.settings.price_error_min_discount_percent,
                    )

                decision = await record_exact_offer(
                    self.db,
                    product=product,
                    offer=offer,
                    # Cheap products still build exact history, but an impossible
                    # threshold prevents them from creating an outbox event.
                    min_event_discount_percent=(
                        self.settings.price_error_min_discount_percent if big_ticket else 100
                    ),
                    normal_interval_minutes=self.settings.normal_offer_interval_minutes,
                    markdown_interval_seconds=self.settings.markdown_offer_interval_seconds,
                )
                verified += 1
                (big_ticket_keys if big_ticket else background_keys).append(product.product_key)

                if not (decision.should_publish and price_error):
                    continue

                candidate = _candidate_for_hp_offer(product, offer, decision)
                candidate.deal_lane = "verified_price_error"
                candidate.variant_attributes.update(
                    {
                        "hpPriceErrorLane": "yes",
                        "hpBigTicketQualified": "yes",
                        "hpBigTicketMinimumReferencePrice": (
                            f"{self.settings.big_ticket_min_reference_price:.2f}"
                        ),
                        "hpPriceErrorMinimumDiscount": str(
                            self.settings.price_error_min_discount_percent
                        ),
                    }
                )
                candidate.signals.append(
                    "Extreme HP big-ticket markdown independently confirmed"
                )
                inserted = await publish_verified_retailer_event(
                    self.db,
                    event_key=decision.event_key,
                    retailer="hp",
                    product_key=product.product_key,
                    event_type=decision.event_type,
                    candidate=candidate,
                    source_verified_at=datetime.now(timezone.utc).isoformat(),
                )
                events += int(inserted)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - isolate one exact product.
                failures += 1
                await store_offer_failure(
                    self.db,
                    product_keys=[product.product_key],
                    error=f"{type(error).__name__}: {error}",
                )

        await reschedule_products(
            self.db,
            product_keys=big_ticket_keys,
            delay=timedelta(seconds=self.settings.big_ticket_offer_interval_seconds),
        )
        await reschedule_products(
            self.db,
            product_keys=background_keys,
            delay=timedelta(minutes=self.settings.normal_offer_interval_minutes),
        )

        missing = [
            product.product_key
            for product in products
            if product.catalog_entry_id not in returned_ids
        ]
        if missing:
            failures += len(missing)
            await store_offer_failure(
                self.db,
                product_keys=missing,
                error="HP structured price response omitted the exact claimed product identity",
            )
        return len(products), verified, failures, events, confirmations


async def claim_price_error_offer_batch(
    db: Any,
    *,
    limit: int,
    minimum_reference_price: float,
    now: datetime | None = None,
) -> list[CatalogProduct]:
    """Reserve capacity for known big-ticket items, then classify/fill fairly.

    One standalone watcher process owns these claims. Known expensive products
    are selected first, while all unused capacity immediately goes to never-seen
    products and then the slower background catalog. That keeps the fast lane
    responsive without preventing initial catalog warm-up.
    """

    conn = db.require_conn()
    resolved_now = _utc(now)
    now_iso = resolved_now.isoformat()
    threshold_cents = max(1, int(round(float(minimum_reference_price) * 100)))
    total_limit = max(1, int(limit))
    protected_limit = max(1, int(total_limit * 0.75))
    selected: list[CatalogProduct] = []
    selected_keys: set[str] = set()

    async def add_query(sql: str, params: tuple[Any, ...], wanted: int) -> None:
        if wanted <= 0:
            return
        cursor = await conn.execute(sql, params)
        for row in await cursor.fetchall():
            product = _catalog_product(row)
            if product.product_key in selected_keys:
                continue
            selected_keys.add(product.product_key)
            selected.append(product)
            if len(selected) >= total_limit:
                break

    columns = (
        "product_key, product_url, sku, catalog_entry_id, title, image_url, "
        "current_price_cents, reference_price_cents, in_stock"
    )
    await add_query(
        f"""
        SELECT {columns}
        FROM {PRODUCT_TABLE}
        WHERE sku <> ''
          AND catalog_entry_id <> ''
          AND offer_checked_at IS NOT NULL
          AND MAX(
                COALESCE(current_price_cents, 0),
                COALESCE(reference_price_cents, 0)
              ) >= ?
          AND offer_next_check_at <= ?
        ORDER BY COALESCE(offer_checked_at, '') ASC, last_seen_at DESC
        LIMIT ?
        """,
        (threshold_cents, now_iso, protected_limit),
        protected_limit,
    )

    remaining = total_limit - len(selected)
    await add_query(
        f"""
        SELECT {columns}
        FROM {PRODUCT_TABLE}
        WHERE sku <> ''
          AND catalog_entry_id <> ''
          AND offer_checked_at IS NULL
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (remaining,),
        remaining,
    )

    remaining = total_limit - len(selected)
    await add_query(
        f"""
        SELECT {columns}
        FROM {PRODUCT_TABLE}
        WHERE sku <> ''
          AND catalog_entry_id <> ''
          AND offer_next_check_at <= ?
        ORDER BY COALESCE(offer_checked_at, '') ASC, last_seen_at DESC
        LIMIT ?
        """,
        (now_iso, max(remaining * 2, remaining)),
        remaining,
    )
    return selected[:total_limit]


async def reschedule_products(
    db: Any,
    *,
    product_keys: Iterable[str],
    delay: timedelta,
    now: datetime | None = None,
) -> None:
    keys = tuple(dict.fromkeys(str(key) for key in product_keys if str(key)))
    if not keys:
        return
    conn = db.require_conn()
    next_check_at = (_utc(now) + delay).isoformat()
    for product_key in keys:
        await conn.execute(
            f"UPDATE {PRODUCT_TABLE} SET offer_next_check_at = ? WHERE product_key = ?",
            (next_check_at, product_key),
        )
    await conn.commit()


def is_big_ticket_product(
    product: CatalogProduct,
    offer: HPPriceOffer,
    *,
    minimum_reference_price: float,
) -> bool:
    observed_value = max(
        float(offer.current_price),
        float(offer.msrp_price or 0.0),
        float(product.previous_current_price or 0.0),
        float(product.previous_reference_price or 0.0),
    )
    return observed_value >= float(minimum_reference_price)


def is_big_ticket_price_error(
    product: CatalogProduct,
    offer: HPPriceOffer,
    *,
    minimum_reference_price: float,
    minimum_discount_percent: int,
) -> bool:
    reference = best_price_error_reference(product, offer)
    if reference is None or reference < float(minimum_reference_price):
        return False
    if offer.in_stock is False or offer.can_add_to_cart is False:
        return False
    discount = (reference - float(offer.current_price)) / reference * 100.0
    return discount >= max(1, int(minimum_discount_percent))


def best_price_error_reference(
    product: CatalogProduct,
    offer: HPPriceOffer,
) -> float | None:
    current = float(offer.current_price)
    references = [
        float(value)
        for value in (
            offer.msrp_price,
            product.previous_current_price,
            product.previous_reference_price,
        )
        if value is not None and float(value) > current
    ]
    return max(references) if references else None


def _catalog_product(row: Any) -> CatalogProduct:
    return CatalogProduct(
        product_key=str(_row_get(row, "product_key", 0) or ""),
        product_url=str(_row_get(row, "product_url", 1) or ""),
        sku=str(_row_get(row, "sku", 2) or ""),
        catalog_entry_id=str(_row_get(row, "catalog_entry_id", 3) or ""),
        title=str(_row_get(row, "title", 4) or ""),
        image_url=str(_row_get(row, "image_url", 5) or ""),
        previous_current_price=_from_cents(_row_get(row, "current_price_cents", 6)),
        previous_reference_price=_from_cents(_row_get(row, "reference_price_cents", 7)),
        previous_in_stock=_db_bool(_row_get(row, "in_stock", 8)),
    )


def _row_get(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (TypeError, IndexError):
            return None


def _from_cents(value: Any) -> float | None:
    try:
        cents = int(value)
    except (TypeError, ValueError):
        return None
    return round(cents / 100.0, 2)


def _db_bool(value: Any) -> bool | None:
    if value is None:
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def _utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)
