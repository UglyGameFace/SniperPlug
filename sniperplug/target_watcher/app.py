from __future__ import annotations

import asyncio
import logging

from sniperplug.storage.local_sqlite import configure_local_sqlite_connection
from sniperplug.storage.process_database import create_runtime_database
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
    db = create_runtime_database(resolved.database_path)
    await db.connect()
    try:
        await configure_local_sqlite_connection(db)
        await db.init()
        backend = str(getattr(db, "backend", "unknown") or "unknown")
        log.info(
            "Target watcher starting backend=%s "
            "sitemap_batch=%s offer_batch=%s locations_per_cycle=%s "
            "products_per_location=%s big_ticket_floor=$%.2f "
            "price_error_floor=%s%% leased_work=yes multitenant_locations=yes "
            "local_sqlite=%s process_isolated_turso=%s",
            backend,
            resolved.sitemap_batch_size,
            resolved.product_batch_size,
            resolved.locations_per_cycle,
            resolved.products_per_location_batch,
            resolved.big_ticket_min_reference_price,
            resolved.price_error_min_discount_percent,
            str(backend == "sqlite").lower(),
            str(backend == "turso-process-isolated").lower(),
        )
        await ProductionTargetWatcherService(db, resolved).run_forever()
    finally:
        await db.close()


def main() -> None:
    asyncio.run(run())
