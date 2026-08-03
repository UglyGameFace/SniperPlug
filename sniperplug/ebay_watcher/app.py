from __future__ import annotations

import asyncio
import logging

from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.service import EbayWatcherService
from sniperplug.storage.db import Database


log = logging.getLogger("sniperplug.ebay_watcher")


async def run(settings: EbayWatcherSettings | None = None) -> None:
    resolved = settings or EbayWatcherSettings.from_env()
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
            "Standalone eBay watcher starting backend=%s env=%s marketplace=%s "
            "rule_batch=%s tracked_batch=%s discount_floor=%s%% "
            "big_ticket_floor=$%.2f sought_floor=$%.2f",
            getattr(db, "backend", "unknown"),
            resolved.environment,
            resolved.marketplace_id,
            resolved.rule_batch_size,
            resolved.tracked_batch_size,
            resolved.default_min_discount_percent,
            resolved.big_ticket_min_reference_price,
            resolved.sought_after_min_reference_price,
        )
        await EbayWatcherService(db, resolved).run_forever()
    finally:
        await db.close()


def main() -> None:
    asyncio.run(run())
