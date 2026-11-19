from __future__ import annotations

import asyncio
import logging

from sniperplug.ocr_reader.config import OcrReaderSettings
from sniperplug.ocr_reader.service import OcrReaderService, current_rss_mb
from sniperplug.storage.local_sqlite import configure_local_sqlite_connection
from sniperplug.storage.process_database import create_runtime_database


log = logging.getLogger("sniperplug.ocr_reader")


async def run(settings: OcrReaderSettings | None = None) -> None:
    resolved = settings or OcrReaderSettings.from_env()
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
            "SniperPlug picture reader starting backend=%s rss_mb=%.1f "
            "local_sqlite=%s process_isolated_turso=%s",
            backend,
            current_rss_mb(),
            str(backend == "sqlite").lower(),
            str(backend == "turso-process-isolated").lower(),
        )
        await OcrReaderService(db, resolved).run_forever()
    finally:
        await db.close()


def main() -> None:
    asyncio.run(run())
