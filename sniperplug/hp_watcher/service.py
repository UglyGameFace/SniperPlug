from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from sniperplug.hp_watcher.client import HPStoreClient
from sniperplug.hp_watcher.config import HPWatcherSettings
from sniperplug.hp_watcher.parser import (
    HPPriceOffer,
    hp_us_product_urls,
    parse_hp_services_price_response,
    parse_product_page_identity,
    parse_sitemap_xml,
)
from sniperplug.hp_watcher.storage import (
    CatalogProduct,
    claim_due_sitemap_sources,
    claim_products_for_offer_poll,
    claim_products_for_page_refresh,
    complete_sitemap_source,
    ensure_hp_watcher_tables,
    hp_watcher_counts,
    record_exact_offer,
    set_health_value,
    store_offer_failure,
    store_product_identity,
    store_product_page_failure,
    upsert_product_urls,
    upsert_sitemap_sources,
)
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.verified_retailer_events import (
    ensure_verified_retailer_event_table,
    publish_verified_retailer_event,
)


log = logging.getLogger("sniperplug.hp_watcher")


@dataclass(frozen=True)
class HPWatcherCycleResult:
    sitemaps_checked: int = 0
    sitemap_failures: int = 0
    product_urls_found: int = 0
    new_products: int = 0
    product_pages_checked: int = 0
    product_page_failures: int = 0
    offers_requested: int = 0
    offers_verified: int = 0
    offer_failures: int = 0
    events_published: int = 0

    def summary_line(self) -> str:
        return (
            "HP watcher cycle: "
            f"sitemaps={self.sitemaps_checked} failures={self.sitemap_failures} "
            f"product_urls={self.product_urls_found} new_products={self.new_products} "
            f"pages={self.product_pages_checked} page_failures={self.product_page_failures} "
            f"offers={self.offers_verified}/{self.offers_requested} "
            f"offer_failures={self.offer_failures} events={self.events_published}"
        )


class HPWatcherService:
    def __init__(self, db: Any, settings: HPWatcherSettings):
        self.db = db
        self.settings = settings

    async def initialize(self) -> None:
        await ensure_hp_watcher_tables(self.db)
        await ensure_verified_retailer_event_table(self.db)
        await upsert_sitemap_sources(self.db, [self.settings.sitemap_index_url])
        await set_health_value(self.db, "service_status", "starting")

    async def run_forever(self) -> None:
        await self.initialize()
        async with HPStoreClient(self.settings) as client:
            while True:
                started = datetime.now(timezone.utc)
                try:
                    result = await self.run_cycle(client)
                    await set_health_value(self.db, "service_status", "healthy")
                    await set_health_value(self.db, "last_successful_cycle_at", started.isoformat())
                    await set_health_value(self.db, "last_cycle_summary", result.summary_line())
                    counts = await hp_watcher_counts(self.db)
                    await set_health_value(
                        self.db,
                        "coverage_counts",
                        ",".join(f"{key}={value}" for key, value in sorted(counts.items())),
                    )
                    log.info("%s counts=%s", result.summary_line(), counts)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - service must survive one failed cycle.
                    await set_health_value(self.db, "service_status", "degraded")
                    await set_health_value(
                        self.db,
                        "last_cycle_error",
                        f"{type(error).__name__}: {error}",
                    )
                    log.exception("HP watcher cycle failed safely")

                if self.settings.run_once:
                    return
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                await asyncio.sleep(max(1.0, self.settings.loop_seconds - elapsed))

    async def run_cycle(self, client: HPStoreClient) -> HPWatcherCycleResult:
        sitemap_counts = await self._process_sitemaps(client)
        page_counts = await self._process_product_pages(client)
        offer_counts = await self._process_offers(client)
        return HPWatcherCycleResult(
            sitemaps_checked=sitemap_counts[0],
            sitemap_failures=sitemap_counts[1],
            product_urls_found=sitemap_counts[2],
            new_products=sitemap_counts[3],
            product_pages_checked=page_counts[0],
            product_page_failures=page_counts[1],
            offers_requested=offer_counts[0],
            offers_verified=offer_counts[1],
            offer_failures=offer_counts[2],
            events_published=offer_counts[3],
        )

    async def _process_sitemaps(self, client: HPStoreClient) -> tuple[int, int, int, int]:
        sources = await claim_due_sitemap_sources(
            self.db,
            limit=self.settings.sitemap_batch_size,
        )
        checked = failures = urls_found = new_products = 0
        for source in sources:
            checked += 1
            try:
                document = await client.fetch_document(
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

                parsed = parse_sitemap_xml(document.text)
                if parsed.kind == "sitemapindex":
                    await upsert_sitemap_sources(self.db, parsed.locations)
                else:
                    product_urls = hp_us_product_urls(parsed)
                    urls_found += len(product_urls)
                    new_products += await upsert_product_urls(self.db, product_urls)
                await complete_sitemap_source(
                    self.db,
                    url=source.url,
                    etag=document.etag,
                    last_modified=document.last_modified,
                    refresh_minutes=self.settings.sitemap_refresh_minutes,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - one sitemap cannot stop others.
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

    async def _process_product_pages(self, client: HPStoreClient) -> tuple[int, int]:
        products = await claim_products_for_page_refresh(
            self.db,
            limit=self.settings.product_page_batch_size,
        )
        if not products:
            return 0, 0

        async def fetch(product: CatalogProduct):
            try:
                document = await client.fetch_document(product.product_url)
                identity = await asyncio.to_thread(
                    parse_product_page_identity,
                    product.product_url,
                    document.text,
                )
                return product, identity, None
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - fail this exact page only.
                return product, None, error

        results = await asyncio.gather(*(fetch(product) for product in products))
        failures = 0
        for product, identity, error in results:
            if identity is None:
                failures += 1
                await store_product_page_failure(
                    self.db,
                    product_key=product.product_key,
                    error=f"{type(error).__name__}: {error}",
                )
                continue
            await store_product_identity(
                self.db,
                product_key=product.product_key,
                identity=identity,
                refresh_hours=self.settings.product_page_refresh_hours,
            )
        return len(products), failures

    async def _process_offers(self, client: HPStoreClient) -> tuple[int, int, int, int]:
        products = await claim_products_for_offer_poll(
            self.db,
            limit=self.settings.offer_batch_size,
        )
        if not products:
            return 0, 0, 0, 0

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
        except Exception as error:  # noqa: BLE001 - batch remains retryable.
            await store_offer_failure(
                self.db,
                product_keys=[product.product_key for product in products],
                error=f"{type(error).__name__}: {error}",
            )
            return len(products), 0, len(products), 0

        verified = failures = events = 0
        returned_ids: set[str] = set()
        for offer in offers:
            product = by_id.get(offer.product_id)
            if product is None:
                continue
            returned_ids.add(offer.product_id)
            try:
                decision = await record_exact_offer(
                    self.db,
                    product=product,
                    offer=offer,
                    min_event_discount_percent=self.settings.min_event_discount_percent,
                    normal_interval_minutes=self.settings.normal_offer_interval_minutes,
                    markdown_interval_seconds=self.settings.markdown_offer_interval_seconds,
                )
                verified += 1
                if decision.should_publish:
                    candidate = _candidate_for_hp_offer(product, offer, decision)
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
            except Exception as error:  # noqa: BLE001 - exact product remains isolated.
                failures += 1
                await store_offer_failure(
                    self.db,
                    product_keys=[product.product_key],
                    error=f"{type(error).__name__}: {error}",
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
        return len(products), verified, failures, events


def _candidate_for_hp_offer(product: CatalogProduct, offer: HPPriceOffer, decision: Any) -> SourceCandidate:
    reference = float(decision.reference_price) if decision.reference_price is not None else None
    current = float(offer.current_price)
    discount = float(decision.discount_percent)
    title = product.title or f"HP product {product.sku}"
    offer_id = f"hp:{product.catalog_entry_id}:{product.sku}"
    reference_source = str(decision.reference_source or "")
    attrs = {
        "hpStructuredPriceProof": "yes",
        "hpCatalogEntryId": product.catalog_entry_id,
        "hpPartNumber": offer.part_number,
        "hpNormalizedSku": offer.sku,
        "referencePriceTrusted": "yes",
        "trustedReferencePrice": f"{reference:.2f}" if reference is not None else "",
        "trustedReferenceSource": reference_source,
        "referencePriceLabel": "HP MSRP" if "lPrice" in reference_source else "Previous exact HP.com price",
        "hpEventType": str(decision.event_type),
        "hpPromotionText": offer.promotion_text,
        "exactRetailer": "hp.com",
    }
    return SourceCandidate(
        source_key="hp_store_watcher",
        retailer="HP",
        title=title,
        product_url=product.product_url,
        direct_product_url=product.product_url,
        current_price=current,
        typical_price=reference,
        image_url=product.image_url or None,
        deal_lane="verified_markdown",
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=discount,
        api_condition="New",
        api_condition_path="hp.services.priceData.productType",
        api_reference_path=reference_source,
        api_price_path="hp.services.priceData.price",
        product_id=product.catalog_entry_id,
        product_id_type="catalog_entry_id",
        sku=product.sku,
        selected_offer_id=offer_id,
        variant_label=product.sku,
        variant_attributes=attrs,
        seller_name="HP.com",
        fulfillment_type="Shipping",
        condition="New",
        stock_status=(
            "In stock" if offer.in_stock is True else "Out of stock" if offer.in_stock is False else "Not returned"
        ),
        can_add_to_cart=offer.can_add_to_cart,
        signals=[
            "Exact HP catalog entry and part number matched structured HPServices priceData",
            "HP strikethrough reference is labeled MSRP, not prevailing market price",
        ],
    )
