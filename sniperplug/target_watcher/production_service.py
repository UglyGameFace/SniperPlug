from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.verified_retailer_events import publish_verified_retailer_event
from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.leased_storage import (
    LeasedTargetCatalogProduct,
    claim_due_sitemap_sources,
    claim_products_for_offer_poll,
    complete_product_work,
    complete_sitemap_source,
    record_exact_offer,
    store_offer_failure,
)
from sniperplug.target_watcher.parser import (
    TargetOffer,
    exact_target_offers_match,
    merge_fulfillment,
    parse_target_fulfillment_response,
    parse_target_product_response,
    parse_target_sitemap,
    target_product_seeds,
)
from sniperplug.target_watcher.service import (
    TargetWatcherService,
    _candidate_for_target_offer,
    offer_requires_exact_confirmation,
)
from sniperplug.target_watcher.storage import (
    upsert_product_seeds,
    upsert_sitemap_sources,
)


class ProductionTargetWatcherService(TargetWatcherService):
    """Production Target watcher with database leases and exact-store proof."""

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
                        source=source,
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
                    source=source,
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
                    source=source,
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

        async def fetch_product(product: LeasedTargetCatalogProduct):
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
        failures = sum(
            1 for _, offer, error in fetched if offer is None or error is not None
        )
        for product, offer, error in fetched:
            if offer is None or error is not None:
                await store_offer_failure(
                    self.db,
                    products=[product],
                    error=f"{type(error).__name__}: {error}",
                )

        if offers_by_tcin:
            batch_products = [
                product for product in products if product.tcin in offers_by_tcin
            ]
            try:
                fulfillment_document = await client.fetch_fulfillment(
                    list(offers_by_tcin)
                )
                fulfillment = await asyncio.to_thread(
                    parse_target_fulfillment_response,
                    fulfillment_document.payload,
                    expected_tcins=list(offers_by_tcin),
                    expected_store_id=self.settings.store_id,
                )
                offers_by_tcin = {
                    tcin: merge_fulfillment(offer, fulfillment.get(tcin))
                    for tcin, offer in offers_by_tcin.items()
                }
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += len(batch_products)
                await store_offer_failure(
                    self.db,
                    products=batch_products,
                    error=(
                        "Target fulfillment verification failed: "
                        f"{type(error).__name__}: {error}"
                    ),
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
                    offer = await confirm_exact_target_offer(
                        client,
                        product,
                        offer,
                        expected_store_id=self.settings.store_id,
                    )
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
                if not await complete_product_work(self.db, product=product):
                    raise RuntimeError(
                        "Target product work lease expired before completion"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                await store_offer_failure(
                    self.db,
                    products=[product],
                    error=f"{type(error).__name__}: {error}",
                )
        return len(products), verified, failures, confirmations, events


async def confirm_exact_target_offer(
    client: TargetRedSkyClient,
    product: LeasedTargetCatalogProduct,
    discovered: TargetOffer,
    *,
    expected_store_id: str,
) -> TargetOffer:
    """Re-fetch one alert-capable TCIN and its exact store before state writes."""

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
        expected_store_id=expected_store_id,
    )
    if product.tcin not in fulfillment:
        raise ValueError("Target confirmation omitted the exact TCIN fulfillment state")
    confirmed = merge_fulfillment(confirmed, fulfillment[product.tcin])
    if not exact_target_offers_match(discovered, confirmed):
        raise ValueError("Target exact price confirmation disagreed with discovery")
    if _availability_signature(discovered) != _availability_signature(confirmed):
        raise ValueError("Target exact availability confirmation disagreed with discovery")
    return confirmed


def _availability_signature(offer: TargetOffer) -> tuple[bool | None, ...]:
    return (
        offer.shipping_available,
        offer.pickup_available,
        offer.can_add_to_cart,
    )
