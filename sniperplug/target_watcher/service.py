from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.verified_retailer_events import (
    ensure_verified_retailer_event_table,
    publish_verified_retailer_event,
)
from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.parser import (
    TargetOffer,
    exact_target_offers_match,
    merge_fulfillment,
    parse_target_fulfillment_response,
    parse_target_product_response,
    parse_target_sitemap,
    target_product_seeds,
)
from sniperplug.target_watcher.storage import (
    TargetCatalogProduct,
    claim_due_sitemap_sources,
    claim_products_for_offer_poll,
    complete_sitemap_source,
    ensure_target_watcher_tables,
    record_exact_offer,
    seed_target_tcins,
    set_health_value,
    store_offer_failure,
    target_watcher_counts,
    upsert_product_seeds,
    upsert_sitemap_sources,
)


log = logging.getLogger("sniperplug.target_watcher")


@dataclass(frozen=True)
class TargetWatcherCycleResult:
    sitemaps_checked: int = 0
    sitemap_failures: int = 0
    product_urls_found: int = 0
    new_products: int = 0
    offers_requested: int = 0
    offers_verified: int = 0
    offer_failures: int = 0
    exact_confirmations: int = 0
    events_published: int = 0

    def summary_line(self) -> str:
        return (
            "Target watcher cycle: "
            f"sitemaps={self.sitemaps_checked} failures={self.sitemap_failures} "
            f"product_urls={self.product_urls_found} new_products={self.new_products} "
            f"offers={self.offers_verified}/{self.offers_requested} "
            f"confirmations={self.exact_confirmations} "
            f"offer_failures={self.offer_failures} events={self.events_published}"
        )


class TargetWatcherService:
    def __init__(self, db: Any, settings: TargetWatcherSettings):
        self.db = db
        self.settings = settings

    async def initialize(self) -> None:
        await ensure_target_watcher_tables(self.db)
        await ensure_verified_retailer_event_table(self.db)
        await upsert_sitemap_sources(self.db, [self.settings.sitemap_index_url])
        if self.settings.watch_tcins:
            await seed_target_tcins(
                self.db,
                self.settings.watch_tcins,
                store_id=self.settings.store_id,
                zip_code=self.settings.zip_code,
            )
        await set_health_value(self.db, "service_status", "starting")

    async def run_forever(self) -> None:
        await self.initialize()
        async with TargetRedSkyClient(self.settings) as client:
            while True:
                started = datetime.now(timezone.utc)
                try:
                    result = await self.run_cycle(client)
                    await set_health_value(self.db, "service_status", "healthy")
                    await set_health_value(
                        self.db,
                        "last_successful_cycle_at",
                        started.isoformat(),
                    )
                    await set_health_value(
                        self.db,
                        "last_cycle_summary",
                        result.summary_line(),
                    )
                    counts = await target_watcher_counts(self.db)
                    await set_health_value(
                        self.db,
                        "coverage_counts",
                        ",".join(
                            f"{key}={value}" for key, value in sorted(counts.items())
                        ),
                    )
                    log.info("%s counts=%s", result.summary_line(), counts)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await set_health_value(self.db, "service_status", "degraded")
                    await set_health_value(
                        self.db,
                        "last_cycle_error",
                        f"{type(error).__name__}: {error}",
                    )
                    log.exception("Target watcher cycle failed safely")

                if self.settings.run_once:
                    return
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                await asyncio.sleep(max(1.0, self.settings.loop_seconds - elapsed))

    async def run_cycle(self, client: TargetRedSkyClient) -> TargetWatcherCycleResult:
        sitemap = await self._process_sitemaps(client)
        offers = await self._process_offers(client)
        return TargetWatcherCycleResult(
            sitemaps_checked=sitemap[0],
            sitemap_failures=sitemap[1],
            product_urls_found=sitemap[2],
            new_products=sitemap[3],
            offers_requested=offers[0],
            offers_verified=offers[1],
            offer_failures=offers[2],
            exact_confirmations=offers[3],
            events_published=offers[4],
        )

    async def _process_sitemaps(
        self,
        client: TargetRedSkyClient,
    ) -> tuple[int, int, int, int]:
        sources = await claim_due_sitemap_sources(
            self.db,
            limit=self.settings.sitemap_batch_size,
        )
        checked = failures = urls_found = new_products = 0
        for source in sources:
            checked += 1
            try:
                document = await client.fetch_sitemap(
                    source.url,
                    etag=source.etag,
                    last_modified=source.last_modified,
                )
                if document.not_modified:
                    await complete_sitemap_source(
                        self.db,
                        url=source.url,
                        etag=document.etag,
                        last_modified=document.last_modified,
                        refresh_minutes=self.settings.sitemap_refresh_minutes,
                    )
                    continue
                parsed = await asyncio.to_thread(
                    parse_target_sitemap,
                    document.body,
                    max_expanded_bytes=self.settings.sitemap_max_expanded_bytes,
                )
                if parsed.kind == "sitemapindex":
                    await upsert_sitemap_sources(self.db, parsed.locations)
                else:
                    seeds = target_product_seeds(parsed)
                    urls_found += len(seeds)
                    new_products += await upsert_product_seeds(
                        self.db,
                        seeds,
                        store_id=self.settings.store_id,
                        zip_code=self.settings.zip_code,
                    )
                await complete_sitemap_source(
                    self.db,
                    url=source.url,
                    etag=document.etag,
                    last_modified=document.last_modified,
                    refresh_minutes=self.settings.sitemap_refresh_minutes,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                await complete_sitemap_source(
                    self.db,
                    url=source.url,
                    etag=source.etag,
                    last_modified=source.last_modified,
                    refresh_minutes=self.settings.sitemap_refresh_minutes,
                    error=f"{type(error).__name__}: {error}",
                )
        return checked, failures, urls_found, new_products

    async def _process_offers(
        self,
        client: TargetRedSkyClient,
    ) -> tuple[int, int, int, int, int]:
        products = await claim_products_for_offer_poll(
            self.db,
            limit=self.settings.product_batch_size,
            big_ticket_min_reference_price=self.settings.big_ticket_min_reference_price,
            price_error_min_discount_percent=self.settings.price_error_min_discount_percent,
        )
        if not products:
            return 0, 0, 0, 0, 0

        async def fetch_product(product: TargetCatalogProduct):
            try:
                document = await client.fetch_product(product.tcin)
                offer = await asyncio.to_thread(
                    parse_target_product_response,
                    document.payload,
                    expected_tcin=product.tcin,
                )
                return product, offer, None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return product, None, error

        fetched = await asyncio.gather(*(fetch_product(product) for product in products))
        offers_by_tcin: dict[str, TargetOffer] = {
            product.tcin: offer
            for product, offer, error in fetched
            if offer is not None and error is None
        }
        failures = sum(1 for _, offer, error in fetched if offer is None or error is not None)
        for product, offer, error in fetched:
            if offer is None or error is not None:
                await store_offer_failure(
                    self.db,
                    product_keys=[product.product_key],
                    error=f"{type(error).__name__}: {error}",
                )

        if offers_by_tcin:
            try:
                fulfillment_document = await client.fetch_fulfillment(
                    list(offers_by_tcin)
                )
                fulfillment = await asyncio.to_thread(
                    parse_target_fulfillment_response,
                    fulfillment_document.payload,
                    expected_tcins=list(offers_by_tcin),
                )
                offers_by_tcin = {
                    tcin: merge_fulfillment(offer, fulfillment.get(tcin))
                    for tcin, offer in offers_by_tcin.items()
                }
            except asyncio.CancelledError:
                raise
            except Exception as error:
                keys = [
                    product.product_key
                    for product in products
                    if product.tcin in offers_by_tcin
                ]
                failures += len(keys)
                await store_offer_failure(
                    self.db,
                    product_keys=keys,
                    error=f"Target fulfillment verification failed: {type(error).__name__}: {error}",
                )
                offers_by_tcin = {}

        verified = confirmations = events = 0
        for product in products:
            offer = offers_by_tcin.get(product.tcin)
            if offer is None:
                continue
            try:
                if offer_requires_exact_confirmation(
                    product,
                    offer,
                    min_discount=self.settings.min_event_discount_percent,
                ):
                    offer = await confirm_exact_target_offer(client, product, offer)
                    confirmations += 1
                decision = await record_exact_offer(
                    self.db,
                    product=product,
                    offer=offer,
                    min_event_discount_percent=self.settings.min_event_discount_percent,
                    normal_interval_minutes=self.settings.normal_offer_interval_minutes,
                    markdown_interval_seconds=self.settings.markdown_offer_interval_seconds,
                    big_ticket_min_reference_price=self.settings.big_ticket_min_reference_price,
                    price_error_min_discount_percent=self.settings.price_error_min_discount_percent,
                    big_ticket_interval_seconds=self.settings.big_ticket_offer_interval_seconds,
                )
                verified += 1
                if decision.should_publish:
                    candidate = _candidate_for_target_offer(
                        product,
                        offer,
                        decision,
                        settings=self.settings,
                    )
                    inserted = await publish_verified_retailer_event(
                        self.db,
                        event_key=decision.event_key,
                        retailer="target",
                        product_key=product.product_key,
                        event_type=decision.event_type,
                        candidate=candidate,
                        source_verified_at=datetime.now(timezone.utc).isoformat(),
                    )
                    events += int(inserted)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                await store_offer_failure(
                    self.db,
                    product_keys=[product.product_key],
                    error=f"{type(error).__name__}: {error}",
                )
        return len(products), verified, failures, confirmations, events


async def confirm_exact_target_offer(
    client: TargetRedSkyClient,
    product: TargetCatalogProduct,
    discovered: TargetOffer,
) -> TargetOffer:
    """Re-fetch one alert-capable TCIN and its fulfillment before state/event writes."""

    await asyncio.sleep(0.75)
    product_document, fulfillment_document = await asyncio.gather(
        client.fetch_product(product.tcin, cache_bust=True),
        client.fetch_fulfillment([product.tcin], cache_bust=True),
    )
    confirmed = await asyncio.to_thread(
        parse_target_product_response,
        product_document.payload,
        expected_tcin=product.tcin,
    )
    fulfillment = await asyncio.to_thread(
        parse_target_fulfillment_response,
        fulfillment_document.payload,
        expected_tcins=[product.tcin],
    )
    if product.tcin not in fulfillment:
        raise ValueError("Target confirmation omitted the exact TCIN fulfillment state")
    confirmed = merge_fulfillment(confirmed, fulfillment[product.tcin])
    if not exact_target_offers_match(discovered, confirmed):
        raise ValueError("Target exact price confirmation disagreed with discovery")
    if _availability_signature(discovered) != _availability_signature(confirmed):
        raise ValueError("Target exact availability confirmation disagreed with discovery")
    return confirmed


def offer_requires_exact_confirmation(
    product: TargetCatalogProduct,
    offer: TargetOffer,
    *,
    min_discount: int,
) -> bool:
    reference = _best_reference_price(product, offer)
    if reference is None or reference <= offer.current_price:
        return False
    discount = (reference - offer.current_price) / reference * 100.0
    if discount < max(1, int(min_discount)):
        return False
    if _offer_available(offer) is not True or offer.can_add_to_cart is False:
        return False
    if product.previous_current_price is None:
        return offer.regular_price is not None
    if offer.current_price < product.previous_current_price:
        return True
    if product.previous_available is False:
        return True
    return bool(
        product.previous_promotion_text
        and offer.promotion_text
        and product.previous_promotion_text != offer.promotion_text
    )


def _candidate_for_target_offer(
    product: TargetCatalogProduct,
    offer: TargetOffer,
    decision: Any,
    *,
    settings: TargetWatcherSettings,
) -> SourceCandidate:
    reference = (
        float(decision.reference_price)
        if decision.reference_price is not None
        else None
    )
    current = float(offer.current_price)
    discount = float(decision.discount_percent)
    price_error = bool(
        reference is not None
        and reference >= settings.big_ticket_min_reference_price
        and discount >= settings.price_error_min_discount_percent
    )
    reference_source = str(decision.reference_source or "")
    attrs = {
        "targetStructuredPriceProof": "yes",
        "targetIndependentConfirmation": "yes",
        "targetTcin": offer.tcin,
        "targetStoreId": product.store_id,
        "targetZip": product.zip_code,
        "targetState": settings.state,
        "referencePriceTrusted": "yes",
        "trustedReferencePrice": f"{reference:.2f}" if reference is not None else "",
        "trustedReferenceSource": reference_source,
        "referencePriceLabel": (
            "Target regular price"
            if reference_source == "target.redsky.product.price.reg_retail"
            else "Previous exact Target price"
        ),
        "targetEventType": str(decision.event_type),
        "targetPromotionText": offer.promotion_text,
        "targetShippingAvailable": _bool_text(offer.shipping_available),
        "targetPickupAvailable": _bool_text(offer.pickup_available),
        "targetPriceErrorLane": "yes" if price_error else "no",
        "targetBigTicketFloor": f"{settings.big_ticket_min_reference_price:.2f}",
        "targetPriceErrorDiscountFloor": str(
            settings.price_error_min_discount_percent
        ),
        "exactRetailer": "target.com",
    }
    attrs.update({f"targetVariant_{key}": value for key, value in offer.variant_attributes.items()})
    fulfillment = " + ".join(
        label
        for label, enabled in (
            ("Shipping", offer.shipping_available),
            ("Pickup/Drive Up", offer.pickup_available),
        )
        if enabled is True
    ) or "Target fulfillment"
    return SourceCandidate(
        source_key="target_redsky_watcher",
        retailer="Target",
        title=offer.title,
        product_url=offer.product_url,
        direct_product_url=offer.product_url,
        current_price=current,
        typical_price=reference,
        image_url=offer.image_url or product.image_url or None,
        deal_lane="price_error" if price_error else "verified_markdown",
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=discount,
        api_condition="New",
        api_condition_path="target.redsky.product.tcin",
        api_reference_path=reference_source,
        api_price_path="target.redsky.product.price.current_retail",
        product_id=offer.tcin,
        product_id_type="tcin",
        sku=offer.tcin,
        selected_offer_id=f"target:{product.store_id}:{offer.tcin}",
        variant_label=offer.variant_label or offer.tcin,
        variant_attributes=attrs,
        seller_name=offer.seller_name or "Target",
        fulfillment_type=fulfillment,
        condition="New",
        stock_status=offer.stock_status or "Availability verified",
        can_add_to_cart=offer.can_add_to_cart,
        signals=[
            "Exact Target TCIN, price, seller, and fulfillment were independently confirmed"
        ],
    )


def _best_reference_price(
    product: TargetCatalogProduct,
    offer: TargetOffer,
) -> float | None:
    if offer.regular_price is not None and offer.regular_price > offer.current_price:
        return offer.regular_price
    if (
        product.previous_current_price is not None
        and product.previous_current_price > offer.current_price
    ):
        return product.previous_current_price
    if (
        product.previous_reference_price is not None
        and product.previous_reference_price > offer.current_price
    ):
        return product.previous_reference_price
    return None


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


def _availability_signature(offer: TargetOffer) -> tuple[bool | None, ...]:
    return (
        offer.shipping_available,
        offer.pickup_available,
        offer.can_add_to_cart,
    )


def _bool_text(value: bool | None) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"
