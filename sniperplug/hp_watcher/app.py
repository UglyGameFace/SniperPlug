from __future__ import annotations

import asyncio
import logging

from sniperplug.hp_watcher.config import HPWatcherSettings
from sniperplug.hp_watcher.service import HPWatcherService
from sniperplug.storage.db import Database


log = logging.getLogger("sniperplug.hp_watcher")


async def run(settings: HPWatcherSettings | None = None) -> None:
    resolved = settings or HPWatcherSettings.from_env()
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
            "Standalone HP watcher starting backend=%s sitemap_batch=%s page_batch=%s offer_batch=%s",
            getattr(db, "backend", "unknown"),
            resolved.sitemap_batch_size,
            resolved.product_page_batch_size,
            resolved.offer_batch_size,
        )
        await HPWatcherService(db, resolved).run_forever()
    finally:
        await db.close()


def main() -> None:
    asyncio.run(run())
