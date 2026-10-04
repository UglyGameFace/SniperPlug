from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from scripts.export_turso_snapshot import export_connection_to_sqlite


ROOT = Path(__file__).resolve().parents[1]


def test_exporter_direct_cli_imports_repository_package() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/export_turso_snapshot.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Create a verified local SQLite snapshot" in result.stdout


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _AsyncSQLiteSource:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path.as_posix())
        self.connection.row_factory = sqlite3.Row
        self.executed_sql: list[str] = []

    async def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
    ) -> _FakeCursor:
        self.executed_sql.append(str(sql).strip())
        cursor = self.connection.execute(sql, tuple(params or ()))
        if cursor.description is None:
            return _FakeCursor([])
        columns = [str(item[0]) for item in cursor.description]
        rows = [
            {columns[index]: row[index] for index in range(len(columns))}
            for row in cursor.fetchall()
        ]
        return _FakeCursor(rows)

    async def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


def _create_source(path: Path) -> None:
    connection = sqlite3.connect(path.as_posix())
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;

            CREATE TABLE guild_settings (
                guild_id INTEGER PRIMARY KEY,
                deals_channel_id INTEGER,
                label TEXT
            );

            CREATE TABLE walmart_exact_detail_queue (
                item_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                payload TEXT,
                FOREIGN KEY (guild_id) REFERENCES guild_settings(guild_id)
            );

            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL
            );

            CREATE TABLE without_rowid_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE INDEX idx_walmart_queue_guild
                ON walmart_exact_detail_queue(guild_id);

            CREATE TRIGGER trg_guild_settings_audit
            AFTER UPDATE ON guild_settings
            BEGIN
                INSERT INTO audit_log(message) VALUES ('guild settings updated');
            END;

            CREATE VIEW queue_summary AS
            SELECT guild_id, COUNT(*) AS queued
            FROM walmart_exact_detail_queue
            GROUP BY guild_id;
            """
        )
        connection.executemany(
            "INSERT INTO guild_settings(guild_id, deals_channel_id, label) VALUES (?, ?, ?)",
            [
                (100000000000000001, 200000000000000001, "one"),
                (100000000000000002, 200000000000000002, "two"),
            ],
        )
        connection.executemany(
            "INSERT INTO walmart_exact_detail_queue(item_id, guild_id, payload) VALUES (?, ?, ?)",
            [
                (f"item-{index}", 100000000000000001 if index % 2 == 0 else 100000000000000002, f"payload-{index}")
                for index in range(125)
            ],
        )
        connection.executemany(
            "INSERT INTO without_rowid_state(key, value) VALUES (?, ?)",
            [(f"k-{index:03d}", f"v-{index:03d}") for index in range(125)],
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_read_only_turso_export_preserves_schema_and_data(tmp_path: Path) -> None:
    source_path = tmp_path / "remote.sqlite3"
    output = tmp_path / "migration" / "snapshot.sqlite3"
    _create_source(source_path)
    source = _AsyncSQLiteSource(source_path)

    try:
        result = await export_connection_to_sqlite(
            source,
            output,
            batch_size=100,
        )
    finally:
        source.close()

    assert result["integrity_check"] == "ok"
    assert result["table_rows"]["guild_settings"] == 2
    assert result["table_rows"]["walmart_exact_detail_queue"] == 125
    assert result["table_rows"]["without_rowid_state"] == 125
    assert len(result["sha256"]) == 64
    assert output.exists()

    manifest = json.loads(
        output.with_suffix(output.suffix + ".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["sha256"] == result["sha256"]

    exported = sqlite3.connect(output.as_posix())
    try:
        assert exported.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert exported.execute("PRAGMA foreign_key_check").fetchall() == []
        assert exported.execute(
            "SELECT COUNT(*) FROM walmart_exact_detail_queue"
        ).fetchone()[0] == 125
        assert exported.execute(
            "SELECT COUNT(*) FROM without_rowid_state"
        ).fetchone()[0] == 125
        assert dict(
            exported.execute(
                "SELECT guild_id, queued FROM queue_summary ORDER BY guild_id"
            ).fetchall()
        ) == {
            100000000000000001: 63,
            100000000000000002: 62,
        }
        index_names = {
            row[0]
            for row in exported.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        trigger_names = {
            row[0]
            for row in exported.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert "idx_walmart_queue_guild" in index_names
        assert "trg_guild_settings_audit" in trigger_names

        exported.execute(
            "UPDATE guild_settings SET label='changed' WHERE guild_id=?",
            (100000000000000001,),
        )
        assert exported.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
    finally:
        exported.close()

    source_prefixes = {
        statement.split(None, 1)[0].upper()
        for statement in source.executed_sql
        if statement
    }
    assert source_prefixes <= {"BEGIN", "SELECT", "PRAGMA"}
    assert any("WHERE rowid > ?" in statement for statement in source.executed_sql)
    assert any("OFFSET ?" in statement for statement in source.executed_sql)


@pytest.mark.asyncio
async def test_export_rejects_nonproduction_remote_shape(tmp_path: Path) -> None:
    source_path = tmp_path / "wrong.sqlite3"
    connection = sqlite3.connect(source_path.as_posix())
    try:
        connection.execute("CREATE TABLE guild_settings (guild_id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    source = _AsyncSQLiteSource(source_path)
    output = tmp_path / "snapshot.sqlite3"
    try:
        with pytest.raises(RuntimeError, match="missing required tables"):
            await export_connection_to_sqlite(source, output)
    finally:
        source.close()

    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".manifest.json").exists()


@pytest.mark.asyncio
async def test_export_refuses_to_replace_completed_snapshot_without_override(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "remote.sqlite3"
    _create_source(source_path)
    output = tmp_path / "snapshot.sqlite3"
    output.write_bytes(b"preserve-me")
    source = _AsyncSQLiteSource(source_path)

    try:
        with pytest.raises(FileExistsError, match="Pass --overwrite"):
            await export_connection_to_sqlite(source, output)
    finally:
        source.close()

    assert output.read_bytes() == b"preserve-me"
