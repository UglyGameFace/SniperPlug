from __future__ import annotations

from typing import Any


LOCAL_SQLITE_BUSY_TIMEOUT_MS = 15_000


async def configure_local_sqlite_connection(db: Any) -> bool:
    """Give independent local worker connections time to serialize writes.

    The canonical bot, HP watcher and Target watcher deliberately use separate
    SQLite connections to the same WAL file. SQLite still allows only one writer
    at a time, so a bounded busy timeout prevents ordinary short write overlap
    from surfacing as an immediate ``database is locked`` failure.
    """

    if str(getattr(db, "backend", "")).strip().lower() != "sqlite":
        return False
    conn = db.require_conn()
    await conn.execute(f"PRAGMA busy_timeout={LOCAL_SQLITE_BUSY_TIMEOUT_MS};")
    await conn.commit()
    return True
