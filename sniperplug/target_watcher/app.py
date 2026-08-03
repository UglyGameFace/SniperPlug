from __future__ import annotations

import asyncio
import logging

from sniperplug.storage.db import Database
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.production_service import ProductionTargetWatcherService


log = logging.getLogger("sniperplug.target_watcher")


async def run(settings: TargetWatcherSettings | None = None) -> None:
    resolved = settings or TargetWatcherSettings.from_env()
    resolved.validate_runtime()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db = Database(resolved.database_path)
    await db.connect()
    try:
        await db.init()
        log.info(
            "Standalone Target watcher starting backend=%s "
            "sitemap_batch=%s offer_batch=%s locations_per_cycle=%s "
            "products_per_location=%s big_ticket_floor=$%.2f "
            "price_error_floor=%s%% leased_work=yes multitenant_locations=yes",
            getattr(db, "backend", "unknown"),
            resolved.sitemap_batch_size,
            resolved.product_batch_size,
            resolved.locations_per_cycle,
            resolved.products_per_location_batch,
            resolved.big_ticket_min_reference_price,
            resolved.price_error_min_discount_percent,
        )
        await ProductionTargetWatcherService(db, resolved).run_forever()
    finally:
        await db.close()


def main() -> None:
    asyncio.run(run())
