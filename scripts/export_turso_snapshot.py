from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from dotenv import load_dotenv

from sniperplug.storage.process_database import SnowflakeSafeLibsqlProcessConnection


REQUIRED_PRODUCTION_TABLES = {
    "guild_settings",
    "walmart_exact_detail_queue",
}
DEFAULT_BATCH_SIZE = 2000


def quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(key): row[key] for key in keys()}
    raise TypeError(f"Expected mapping-like database row, got {type(row).__name__}")


async def _fetchall(source: Any, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    cursor = await source.execute(sql, params)
    return [_row_dict(row) for row in await cursor.fetchall()]


async def _schema_rows(source: Any) -> list[dict[str, Any]]:
    return await _fetchall(
        source,
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND name NOT LIKE 'sqlite_%'
        ORDER BY
          CASE type
            WHEN 'table' THEN 0
            WHEN 'index' THEN 1
            WHEN 'trigger' THEN 2
            WHEN 'view' THEN 3
            ELSE 4
          END,
          name
        """,
    )


async def _table_columns(source: Any, table: str) -> list[str]:
    rows = await _fetchall(source, f"PRAGMA table_info({quote_identifier(table)})")
    columns = [str(row.get("name") or "") for row in rows]
    columns = [column for column in columns if column]
    if not columns:
        raise RuntimeError(f"Remote table {table!r} exposed no columns")
    return columns


def _uses_hidden_rowid(create_sql: str, columns: list[str]) -> bool:
    if "WITHOUT ROWID" in str(create_sql or "").upper():
        return False
    lowered = {column.lower() for column in columns}
    return not bool(lowered.intersection({"rowid", "_rowid_", "oid"}))


async def _copy_table(
    source: Any,
    destination: sqlite3.Connection,
    *,
    table: str,
    create_sql: str,
    batch_size: int,
) -> int:
    columns = await _table_columns(source, table)
    quoted_table = quote_identifier(table)
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = (
        f"INSERT INTO {quoted_table} ({quoted_columns}) "
        f"VALUES ({placeholders})"
    )
    copied = 0

    if _uses_hidden_rowid(create_sql, columns):
        last_rowid: int | None = None
        while True:
            if last_rowid is None:
                sql = (
                    f'SELECT rowid AS "__sp_export_rowid__", {quoted_columns} '
                    f"FROM {quoted_table} ORDER BY rowid LIMIT ?"
                )
                params: tuple[Any, ...] = (batch_size,)
            else:
                sql = (
                    f'SELECT rowid AS "__sp_export_rowid__", {quoted_columns} '
                    f"FROM {quoted_table} WHERE rowid > ? ORDER BY rowid LIMIT ?"
                )
                params = (last_rowid, batch_size)

            rows = await _fetchall(source, sql, params)
            if not rows:
                break

            payload = [tuple(row.get(column) for column in columns) for row in rows]
            destination.executemany(insert_sql, payload)
            destination.commit()
            copied += len(rows)
            last_rowid = int(rows[-1]["__sp_export_rowid__"])
            print(f"  {table}: copied {copied} rows", flush=True)
            if len(rows) < batch_size:
                break
        return copied

    offset = 0
    while True:
        rows = await _fetchall(
            source,
            f"SELECT {quoted_columns} FROM {quoted_table} LIMIT ? OFFSET ?",
            (batch_size, offset),
        )
        if not rows:
            break
        payload = [tuple(row.get(column) for column in columns) for row in rows]
        destination.executemany(insert_sql, payload)
        destination.commit()
        copied += len(rows)
        offset += len(rows)
        print(f"  {table}: copied {copied} rows", flush=True)
        if len(rows) < batch_size:
            break
    return copied


def _verify_snapshot(path: Path, required_tables: set[str]) -> dict[str, Any]:
    connection = sqlite3.connect(path.as_posix(), timeout=30.0)
    try:
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0] if integrity_row else "").strip()
        if integrity.lower() != "ok":
            raise RuntimeError(f"Exported SQLite integrity_check failed: {integrity or 'no result'}")

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(required_tables - tables)
        if missing:
            raise RuntimeError(
                "Exported snapshot is missing required production tables: "
                + ", ".join(missing)
            )

        violations = connection.execute("PRAGMA foreign_key_check").fetchmany(25)
        if violations:
            raise RuntimeError(
                "Exported snapshot failed foreign_key_check; first violations: "
                + repr(violations[:5])
            )

        return {
            "integrity_check": integrity,
            "table_count": len(tables),
            "required_tables_present": sorted(required_tables),
            "bytes": path.stat().st_size,
        }
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def export_connection_to_sqlite(
    source: Any,
    output: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    overwrite: bool = False,
    required_tables: set[str] | None = None,
) -> dict[str, Any]:
    output = output.expanduser().resolve()
    required = set(required_tables or REQUIRED_PRODUCTION_TABLES)
    partial = output.with_suffix(output.suffix + ".partial")

    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output}. Pass --overwrite only after preserving it."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    partial.unlink(missing_ok=True)

    schema: list[dict[str, Any]] = []
    table_counts: dict[str, int] = {}
    transaction_started = False
    destination: sqlite3.Connection | None = None

    try:
        await source.execute("PRAGMA query_only=ON")
        query_only_rows = await _fetchall(source, "PRAGMA query_only")
        if not query_only_rows:
            raise RuntimeError("Could not verify query_only mode on remote database")
        query_only_value = next(iter(query_only_rows[0].values()), 0)
        if int(query_only_value or 0) != 1:
            raise RuntimeError("Remote database did not enter query_only mode")

        await source.execute("BEGIN")
        transaction_started = True
        schema = await _schema_rows(source)
        table_rows = [row for row in schema if str(row.get("type")) == "table"]
        table_names = {str(row.get("name") or "") for row in table_rows}
        missing_remote = sorted(required - table_names)
        if missing_remote:
            raise RuntimeError(
                "Remote database does not look like current SniperPlug production; "
                "missing required tables: " + ", ".join(missing_remote)
            )

        destination = sqlite3.connect(partial.as_posix(), timeout=30.0)
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.execute("PRAGMA synchronous=FULL")
        destination.execute("PRAGMA foreign_keys=OFF")

        for row in table_rows:
            name = str(row["name"])
            create_sql = str(row["sql"])
            destination.execute(create_sql)
        destination.commit()

        print(
            f"Remote schema accepted: {len(table_rows)} user tables. "
            "Beginning read-only export...",
            flush=True,
        )
        for row in table_rows:
            name = str(row["name"])
            table_counts[name] = await _copy_table(
                source,
                destination,
                table=name,
                create_sql=str(row["sql"]),
                batch_size=max(100, min(int(batch_size), 10000)),
            )

        for object_type in ("index", "trigger", "view"):
            for row in schema:
                if str(row.get("type")) != object_type:
                    continue
                sql = str(row.get("sql") or "").strip()
                if sql:
                    destination.execute(sql)
            destination.commit()

        await source.rollback()
        transaction_started = False

        destination.execute("PRAGMA foreign_keys=ON")
        destination.commit()
        destination.close()
        destination = None

        verification = _verify_snapshot(partial, required)
        if output.exists():
            output.unlink()
        partial.replace(output)

        result = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "output": output.as_posix(),
            "sha256": _sha256(output),
            "table_rows": table_counts,
            **verification,
        }
        manifest = output.with_suffix(output.suffix + ".manifest.json")
        manifest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result
    except BaseException:
        if destination is not None:
            try:
                destination.close()
            except Exception:
                pass
        if transaction_started:
            try:
                await source.rollback()
            except Exception:
                pass
        raise


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv()
    database_url = (
        os.getenv("TURSO_DATABASE_URL", "").strip()
        or os.getenv("LIBSQL_URL", "").strip()
    )
    auth_token = (
        os.getenv("TURSO_AUTH_TOKEN", "").strip()
        or os.getenv("LIBSQL_AUTH_TOKEN", "").strip()
    )
    if not database_url or not auth_token:
        raise RuntimeError(
            "Turso/libSQL credentials are required. Keep the existing production "
            "TURSO_DATABASE_URL/TURSO_AUTH_TOKEN set while running this recovery export."
        )

    source = await SnowflakeSafeLibsqlProcessConnection.open(
        database=database_url,
        auth_token=auth_token,
        startup_timeout_seconds=max(5.0, float(args.startup_timeout)),
        operation_timeout_seconds=max(10.0, float(args.operation_timeout)),
    )
    try:
        probe = await source.execute("SELECT 1 AS readable")
        row = await probe.fetchone()
        if not row:
            raise RuntimeError("Turso read probe returned no row")
        return await export_connection_to_sqlite(
            source,
            args.output,
            batch_size=args.batch_size,
            overwrite=args.overwrite,
        )
    finally:
        await source.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a verified local SQLite snapshot by reading SniperPlug's current "
            "Turso/libSQL database. The exporter performs no writes to Turso."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./migration/sniperplug-turso-export.sqlite3"),
        help="Snapshot output path (default: ./migration/sniperplug-turso-export.sqlite3)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Rows fetched per remote read batch (100-10000; default: 2000)",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=45.0,
        help="libSQL worker startup timeout in seconds",
    )
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=90.0,
        help="Per-query timeout in seconds",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing completed export; stale .partial files are always replaced",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Turso export cancelled; completed output was not replaced.", file=sys.stderr)
        return 130
    except Exception as error:
        print(
            f"Turso export FAILED: {type(error).__name__}: {error}\n"
            "Any .partial file is incomplete and must not be installed.",
            file=sys.stderr,
        )
        return 1

    print(
        "Turso export COMPLETE\n"
        f"Snapshot: {result['output']}\n"
        f"Tables: {result['table_count']}\n"
        f"Bytes: {result['bytes']}\n"
        f"SHA256: {result['sha256']}\n"
        "Integrity check: ok",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
