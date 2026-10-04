from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3


log = logging.getLogger("sniperplug.sqlite_backup")
DEFAULT_BACKUP_INTERVAL_SECONDS = 6 * 60 * 60
DEFAULT_BACKUP_RETENTION = 14


def local_sqlite_backups_enabled(db: object) -> bool:
    return str(getattr(db, "backend", "")).strip().lower() == "sqlite"


async def create_local_sqlite_backup(
    db: object,
    *,
    backup_dir: str | Path | None = None,
    retention: int = DEFAULT_BACKUP_RETENTION,
    now: datetime | None = None,
) -> Path | None:
    """Create and integrity-check one online SQLite snapshot.

    A separate sqlite3 connection uses SQLite's online backup API, so WAL-backed
    production writes may continue while the snapshot is copied. The completed
    file is published atomically only after ``PRAGMA integrity_check`` passes.
    """

    if not local_sqlite_backups_enabled(db):
        return None

    database_path = Path(str(getattr(db, "path", "") or "")).expanduser()
    if not database_path.is_absolute():
        database_path = (Path.cwd() / database_path).resolve()
    if not database_path.exists():
        return None

    resolved_dir = Path(backup_dir) if backup_dir is not None else database_path.parent / "backups"
    if not resolved_dir.is_absolute():
        resolved_dir = (Path.cwd() / resolved_dir).resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = resolved_dir / f"sniperplug-{stamp}.sqlite3"
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    await asyncio.to_thread(_copy_and_verify, database_path, temporary)
    os.replace(temporary, destination)
    await asyncio.to_thread(_prune_backups, resolved_dir, max(1, int(retention)))
    log.info(
        "Local SQLite backup verified path=%s bytes=%s retention=%s",
        destination,
        destination.stat().st_size,
        max(1, int(retention)),
    )
    return destination


async def run_local_sqlite_backup_loop(
    db: object,
    *,
    interval_seconds: float = DEFAULT_BACKUP_INTERVAL_SECONDS,
    retention: int = DEFAULT_BACKUP_RETENTION,
) -> None:
    """Keep bounded on-disk recovery snapshots while local SQLite is active."""

    if not local_sqlite_backups_enabled(db):
        return

    delay = max(15 * 60.0, float(interval_seconds))
    while True:
        try:
            await create_local_sqlite_backup(db, retention=retention)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Local SQLite backup failed safely; runtime remains online")
        await asyncio.sleep(delay)


def _copy_and_verify(source_path: Path, destination_path: Path) -> None:
    if destination_path.exists():
        destination_path.unlink()

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=30.0)
    destination = sqlite3.connect(destination_path.as_posix(), timeout=30.0)
    try:
        source.backup(destination)
        destination.commit()
        row = destination.execute("PRAGMA integrity_check").fetchone()
        result = str(row[0] if row else "").strip().lower()
        if result != "ok":
            raise RuntimeError(f"SQLite backup integrity_check failed: {result or 'no result'}")
    finally:
        destination.close()
        source.close()


def _prune_backups(directory: Path, retention: int) -> None:
    backups = sorted(
        directory.glob("sniperplug-*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[max(1, int(retention)) :]:
        stale.unlink(missing_ok=True)
