from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.target_locations import (
    TargetLocationContext,
    ensure_target_location_tables,
    get_active_target_location_by_key,
    list_unique_active_target_locations,
    prune_orphan_target_product_rows,
    stage_due_target_location_batches,
    upsert_target_catalog_seeds,
)
from sniperplug.services.verified_retailer_events import (
    ensure_verified_retailer_event_table,
    publish_verified_retailer_event,
)
from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.leased_storage import (
    LeasedTargetCatalogProduct,
    claim_due_sitemap_sources,
    claim_products_for_offer_poll,
    complete_product_work,
    complete_sitemap_source,
    ensure_target_watcher_lease_table,
    record_exact_offer,
    store_offer_failure,
)
from sniperplug.target_watcher.parser import (
    TargetOffer,
    TargetProductSeed,
    canonical_target_product_url,
    exact_target_offers_match,
    merge_fulfillment,
    parse_target_fulfillment_response,
    parse_target_product_response,
    parse_target_sitemap,
    target_product_seeds,
)
from sniperplug.target_watcher.service import (
    TargetWatcherService,
    offer_requires_exact_confirmation,
)
from sniperplug.target_watcher.storage import (
    ensure_target_watcher_tables,
    set_health_value,
    upsert_sitemap_sources,
)


class ProductionTargetWatcherService(TargetWatcherService):
    """Production Target watcher with per-location requests and work leases."""

    async def initialize(self) -> None:
        await ensure_target_watcher_tables(self.db)
        await ensure_target_watcher_lease_table(self.db)
        await ensure_target_location_tables(self.db)
        await ensure_verified_retailer_event_table(self.db)
        await upsert_sitemap_sources(self.db, [self.settings.sitemap_index_url])
        if self.settings.watch_tcins:
            await upsert_target_catalog_seeds(
                self.db,
                [
                    TargetProductSeed(
                        tcin=tcin,
                        product_url=canonical_target_product_url(tcin),
                    )
                    for tcin in self.settings.watch_tcins
                ],
            )
        removed = await prune_orphan_target_product_rows(self.db)
        locations = await list_unique_active_target_locations(self.db)
        await set_health_value(self.db, "service_status", "starting")
        await set_health_value(self.db, "active_unique_locations", str(len(locations)))
        await set_health_value(self.db, "orphan_rows_pruned", str(removed))

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
                    new_products += await upsert_target_catalog_seeds(self.db, seeds)
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
        staged_locations, staged_products = await stage_due_target_location_batches(
            self.db,
            locations_per_cycle=self.settings.locations_per_cycle,
            products_per_location=self.settings.products_per_location_batch,
            scan_spacing_seconds=self.settings.location_scan_spacing_seconds,
        )
        await set_health_value(
            self.db,
            "last_location_staging",
            f"locations={staged_locations},products={staged_products}",
        )
        products = await claim_products_for_offer_poll(
            self.db,
            limit=self.settings.product_batch_size,
            big_ticket_min_reference_price=self.settings.big_ticket_min_reference_price,
            price_error_min_discount_percent=self.settings.price_error_min_discount_percent,
        )
        if not products:
            return 0, 0, 0, 0, 0

        location_cache: dict[str, TargetLocationContext | None] = {}

        async def location_for(product: LeasedTargetCatalogProduct) -> TargetLocationContext | None:
            key = f"{product.store_id}:{product.zip_code}"
            if key not in location_cache:
                location_cache[key] = await get_active_target_location_by_key(
                    self.db,
                    store_id=product.store_id,
                    zip_code=product.zip_code,
                )
            return location_cache[key]

        async def fetch_product(product: LeasedTargetCatalogProduct):
            location = await location_for(product)
            if location is None:
                return product, None, None, RuntimeError(
                    "Target product has no active matching location profile"
                )
            try:
                document = await client.fetch_product(
                    product.tcin,
                    location=location,
                )
                offer = await asyncio.to_thread(
                    parse_target_product_response,
                    document.payload,
                    expected_tcin=product.tcin,
                )
                return product, location, offer, None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return product, location, None, error

        fetched = await asyncio.gather(*(fetch_product(product) for product in products))
        successful: dict[str, tuple[LeasedTargetCatalogProduct, TargetLocationContext, TargetOffer]] = {}
        failures = 0
        for product, location, offer, error in fetched:
            if location is None or offer is None or error is not None:
                failures += 1
                await store_offer_failure(
                    self.db,
                    products=[product],
                    error=f"{type(error).__name__}: {error}",
                )
                continue
            successful[product.product_key] = (product, location, offer)

        grouped: dict[str, list[tuple[LeasedTargetCatalogProduct, TargetLocationContext, TargetOffer]]] = defaultdict(list)
        for entry in successful.values():
            grouped[entry[1].location_key].append(entry)

        verified_fulfillment: dict[str, TargetOffer] = {}
        for entries in grouped.values():
            location = entries[0][1]
            tcins = list(dict.fromkeys(entry[0].tcin for entry in entries))
            try:
                fulfillment_document = await client.fetch_fulfillment(
                    tcins,
                    location=location,
                )
                fulfillment = await asyncio.to_thread(
                    parse_target_fulfillment_response,
                    fulfillment_document.payload,
                    expected_tcins=tcins,
                    expected_store_id=location.store_id,
                )
                for product, _, offer in entries:
                    exact = fulfillment.get(product.tcin)
                    if exact is None:
                        raise ValueError(
                            f"Target fulfillment omitted exact TCIN {product.tcin}"
                        )
                    verified_fulfillment[product.product_key] = merge_fulfillment(
                        offer,
                        exact,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += len(entries)
                await store_offer_failure(
                    self.db,
                    products=[entry[0] for entry in entries],
                    error=(
                        "Target location fulfillment verification failed: "
                        f"{type(error).__name__}: {error}"
                    ),
                )

        verified = confirmations = events = 0
        for product in products:
            entry = successful.get(product.product_key)
            offer = verified_fulfillment.get(product.product_key)
            if entry is None or offer is None:
                continue
            location = entry[1]
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
                        location=location,
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
                    candidate = candidate_for_target_offer(
                        product,
                        offer,
                        decision,
                        location=location,
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
    location: TargetLocationContext,
) -> TargetOffer:
    """Re-fetch one alert-capable TCIN in the exact saved location."""

    await asyncio.sleep(0.75)
    product_document, fulfillment_document = await asyncio.gather(
        client.fetch_product(
            product.tcin,
            location=location,
            cache_bust=True,
        ),
        client.fetch_fulfillment(
            [product.tcin],
            location=location,
            cache_bust=True,
        ),
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
        expected_store_id=location.store_id,
    )
    if product.tcin not in fulfillment:
        raise ValueError("Target confirmation omitted the exact TCIN fulfillment state")
    confirmed = merge_fulfillment(confirmed, fulfillment[product.tcin])
    if not exact_target_offers_match(discovered, confirmed):
        raise ValueError("Target exact price confirmation disagreed with discovery")
    if _availability_signature(discovered) != _availability_signature(confirmed):
        raise ValueError("Target exact availability confirmation disagreed with discovery")
    return confirmed


def candidate_for_target_offer(
    product: LeasedTargetCatalogProduct,
    offer: TargetOffer,
    decision: Any,
    *,
    location: TargetLocationContext,
    settings: Any,
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
        "targetLocationScope": "local",
        "targetTcin": offer.tcin,
        "targetStoreId": location.store_id,
        "targetStoreName": location.store_name,
        "targetZip": location.zip_code,
        "targetState": location.state,
        "targetCity": location.city,
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
    attrs.update(
        {
            f"targetVariant_{key}": value
            for key, value in offer.variant_attributes.items()
        }
    )
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
        selected_offer_id=f"target:{location.store_id}:{offer.tcin}",
        variant_label=offer.variant_label or offer.tcin,
        variant_attributes=attrs,
        seller_name=offer.seller_name or "Target",
        fulfillment_type=fulfillment,
        condition="New",
        stock_status=offer.stock_status or "Availability verified",
        can_add_to_cart=offer.can_add_to_cart,
        signals=[
            "Exact Target TCIN, seller, price, and saved-location fulfillment were independently confirmed"
        ],
    )


def _availability_signature(offer: TargetOffer) -> tuple[bool | None, ...]:
    return (
        offer.shipping_available,
        offer.pickup_available,
        offer.can_add_to_cart,
    )


def _bool_text(value: bool | None) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"
