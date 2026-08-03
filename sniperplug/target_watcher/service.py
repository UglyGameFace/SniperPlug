from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.parser import TargetOffer
from sniperplug.target_watcher.storage import set_health_value, target_watcher_counts


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
    """Location-neutral runner shared by the production implementation.

    Concrete services must provide initialization, sitemap processing, and offer
    processing. Keeping the base class free of store defaults makes accidental
    single-location fallback impossible.
    """

    def __init__(self, db: Any, settings: TargetWatcherSettings):
        self.db = db
        self.settings = settings

    async def initialize(self) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    async def _process_offers(
        self,
        client: TargetRedSkyClient,
    ) -> tuple[int, int, int, int, int]:
        raise NotImplementedError


def offer_requires_exact_confirmation(
    product: Any,
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


def _best_reference_price(product: Any, offer: TargetOffer) -> float | None:
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
