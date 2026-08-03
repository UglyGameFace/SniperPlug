from __future__ import annotations

import asyncio
import logging

from sniperplug.storage.db import Database
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.service import TargetWatcherService


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
            "Standalone Target watcher starting backend=%s store=%s zip=%s "
            "sitemap_batch=%s product_batch=%s big_ticket_floor=$%.2f "
            "price_error_floor=%s%%",
            getattr(db, "backend", "unknown"),
            resolved.store_id,
            resolved.zip_code,
            resolved.sitemap_batch_size,
            resolved.product_batch_size,
            resolved.big_ticket_min_reference_price,
            resolved.price_error_min_discount_percent,
        )
        await TargetWatcherService(db, resolved).run_forever()
    finally:
        await db.close()


def main() -> None:
    asyncio.run(run())
