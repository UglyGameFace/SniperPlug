from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys


REQUIRED_TABLES = {
    "guild_settings",
    "walmart_exact_detail_queue",
}


def sqlite_integrity(path: Path) -> tuple[bool, str]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30.0)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        result = str(row[0] if row else "").strip()
        return result.lower() == "ok", result or "no result"
    finally:
        connection.close()


def sqlite_tables(path: Path) -> set[str]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30.0)
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()


def copy_sqlite_database(source: Path, destination: Path) -> None:
    """Copy one consistent SQLite snapshot, including committed WAL state."""

    destination.unlink(missing_ok=True)
    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro",
        uri=True,
        timeout=30.0,
    )
    destination_connection = sqlite3.connect(destination.as_posix(), timeout=30.0)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def install_snapshot(source: Path, target: Path, *, require_tables: bool = True) -> Path | None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source == target:
        raise RuntimeError("Source and target SQLite paths must be different")
    if not source.is_file():
        raise FileNotFoundError(source)

    ok, detail = sqlite_integrity(source)
    if not ok:
        raise RuntimeError(f"Source SQLite integrity_check failed: {detail}")

    if require_tables:
        missing = sorted(REQUIRED_TABLES - sqlite_tables(source))
        if missing:
            raise RuntimeError(
                "Source snapshot does not look like the SniperPlug production database; "
                f"missing tables: {', '.join(missing)}"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.name}.pre-migration-{stamp}.bak")
        copy_sqlite_database(target, backup)
        backup_ok, backup_detail = sqlite_integrity(backup)
        if not backup_ok:
            backup.unlink(missing_ok=True)
            raise RuntimeError(
                f"Existing local database backup failed integrity_check: {backup_detail}"
            )

    staged = target.with_suffix(target.suffix + ".installing")
    copy_sqlite_database(source, staged)
    staged_ok, staged_detail = sqlite_integrity(staged)
    if not staged_ok:
        staged.unlink(missing_ok=True)
        raise RuntimeError(f"Staged SQLite integrity_check failed: {staged_detail}")

    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    staged.replace(target)
    final_ok, final_detail = sqlite_integrity(target)
    if not final_ok:
        raise RuntimeError(f"Installed SQLite integrity_check failed: {final_detail}")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Integrity-check and atomically install a SQLite snapshot as SniperPlug's "
            "local production database. Stop the SniperPlug app before running this."
        )
    )
    parser.add_argument("source", type=Path, help="SQLite snapshot exported from the old database")
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("./data/sniperplug.sqlite3"),
        help="Local production database path (default: ./data/sniperplug.sqlite3)",
    )
    parser.add_argument(
        "--allow-empty-schema",
        action="store_true",
        help="Skip the SniperPlug table-identity guard (integrity_check still runs)",
    )
    args = parser.parse_args()

    try:
        backup = install_snapshot(
            args.source,
            args.target,
            require_tables=not args.allow_empty_schema,
        )
    except Exception as error:
        print(f"SQLite snapshot install FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(f"SQLite snapshot installed: {args.target.expanduser().resolve()}")
    if backup is not None:
        print(f"Previous local database preserved: {backup}")
    print("Integrity check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
