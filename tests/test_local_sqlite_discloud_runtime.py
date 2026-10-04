from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from scripts.install_sqlite_snapshot import install_snapshot
from sniperplug.process_runtime import (
    LOCAL_SQLITE_ALLOW_EMPTY_ENV,
    _assert_local_database_present,
)
from sniperplug.services.local_sqlite_backup import create_local_sqlite_backup
from sniperplug.storage.db import Database
from sniperplug.storage.local_sqlite import (
    LOCAL_SQLITE_BUSY_TIMEOUT_MS,
    configure_local_sqlite_connection,
)


ROOT = Path(__file__).resolve().parents[1]
PROCESS_RUNTIME = (ROOT / "sniperplug/process_runtime.py").read_text(encoding="utf-8")
HP_RUNTIME = (ROOT / "sniperplug/hp_watcher/app.py").read_text(encoding="utf-8")
TARGET_RUNTIME = (ROOT / "sniperplug/target_watcher/app.py").read_text(encoding="utf-8")
DISCLOUD = (ROOT / "discloud.config").read_text(encoding="utf-8")
DEPLOY_DOC = (ROOT / "docs/DISCLOUD_DEPLOY.md").read_text(encoding="utf-8")


class _FakeDatabase:
    def __init__(self, path: Path, backend: str = "sqlite") -> None:
        self.path = str(path)
        self.backend = backend


def _create_sqlite(path: Path, *, production_shape: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
        connection.execute("INSERT INTO sample (label) VALUES ('preserved')")
        if production_shape:
            connection.execute("CREATE TABLE guild_settings (guild_id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE walmart_exact_detail_queue (item_id TEXT PRIMARY KEY)"
            )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_local_sqlite_backup_is_online_verified_and_readable(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "sniperplug.sqlite3"
    _create_sqlite(database_path)
    db = _FakeDatabase(database_path)

    backup = await create_local_sqlite_backup(
        db,
        backup_dir=tmp_path / "backups",
        now=datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc),
    )

    assert backup is not None
    assert backup.name == "sniperplug-20260917T040000Z.sqlite3"
    connection = sqlite3.connect(backup)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT label FROM sample").fetchone()[0] == "preserved"
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_local_backup_is_a_noop_for_remote_backend(tmp_path: Path) -> None:
    database_path = tmp_path / "sniperplug.sqlite3"
    _create_sqlite(database_path)
    db = _FakeDatabase(database_path, backend="turso-process-isolated")

    assert await create_local_sqlite_backup(db, backup_dir=tmp_path / "backups") is None
    assert not (tmp_path / "backups").exists()


@pytest.mark.asyncio
async def test_local_sqlite_connections_wait_for_short_writer_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "TURSO_DATABASE_URL",
        "TURSO_AUTH_TOKEN",
        "LIBSQL_URL",
        "LIBSQL_AUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    db = Database(str(tmp_path / "sniperplug.sqlite3"))
    await db.connect()
    try:
        assert await configure_local_sqlite_connection(db) is True
        cursor = await db.require_conn().execute("PRAGMA busy_timeout")
        row = await cursor.fetchone()
        assert int(row[0]) == LOCAL_SQLITE_BUSY_TIMEOUT_MS
    finally:
        await db.close()


def test_local_production_refuses_missing_or_empty_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LOCAL_SQLITE_ALLOW_EMPTY_ENV, raising=False)
    missing = tmp_path / "missing.sqlite3"

    with pytest.raises(RuntimeError, match="requires an existing non-empty database"):
        _assert_local_database_present(str(missing))

    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    with pytest.raises(RuntimeError, match="state=empty"):
        _assert_local_database_present(str(empty))


def test_local_production_allows_explicit_one_time_empty_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "fresh.sqlite3"
    monkeypatch.setenv(LOCAL_SQLITE_ALLOW_EMPTY_ENV, "true")

    _assert_local_database_present(str(missing))


def test_snapshot_installer_preserves_existing_db_and_installs_verified_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "export.sqlite3"
    target = tmp_path / "data" / "sniperplug.sqlite3"
    _create_sqlite(source, production_shape=True)
    _create_sqlite(target)

    backup = install_snapshot(source, target)

    assert backup is not None and backup.exists()
    migrated = sqlite3.connect(target)
    previous = sqlite3.connect(backup)
    try:
        assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "guild_settings" in tables
        assert "walmart_exact_detail_queue" in tables
        assert previous.execute("SELECT label FROM sample").fetchone()[0] == "preserved"
    finally:
        migrated.close()
        previous.close()


def test_snapshot_installer_rejects_wrong_database_shape(tmp_path: Path) -> None:
    source = tmp_path / "not-sniperplug.sqlite3"
    _create_sqlite(source)

    with pytest.raises(RuntimeError, match="missing tables"):
        install_snapshot(source, tmp_path / "data" / "sniperplug.sqlite3")


def test_canonical_runtime_embeds_watchers_only_with_explicit_local_sqlite() -> None:
    assert 'LOCAL_SQLITE_MODE_ENV = "SNIPERPLUG_LOCAL_SQLITE"' in PROCESS_RUNTIME
    assert 'LOCAL_SQLITE_ALLOW_EMPTY_ENV = "SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY"' in PROCESS_RUNTIME
    assert "_assert_local_database_present(settings.database_path)" in PROCESS_RUNTIME
    assert 'EMBED_HP_WATCHER_ENV = "SNIPERPLUG_EMBED_HP_WATCHER"' in PROCESS_RUNTIME
    assert 'EMBED_TARGET_WATCHER_ENV = "SNIPERPLUG_EMBED_TARGET_WATCHER"' in PROCESS_RUNTIME
    assert 'backend != "sqlite"' in PROCESS_RUNTIME
    assert "_assert_remote_database_env_absent" in PROCESS_RUNTIME
    assert "require_remote_database=False" in PROCESS_RUNTIME
    assert "run_hp_watcher" in PROCESS_RUNTIME
    assert "run_target_watcher" in PROCESS_RUNTIME
    assert "_supervise_embedded_watcher" in PROCESS_RUNTIME
    assert "run_local_sqlite_backup_loop" in PROCESS_RUNTIME
    assert "configure_local_sqlite_connection" in PROCESS_RUNTIME

    # Each watcher creates its own database connection. In one Discloud app the
    # shared DATABASE_PATH points those independent connections at the same WAL
    # file instead of sharing a transaction stream.
    assert "db = create_runtime_database(resolved.database_path)" in HP_RUNTIME
    assert "db = create_runtime_database(resolved.database_path)" in TARGET_RUNTIME
    assert "configure_local_sqlite_connection(db)" in HP_RUNTIME
    assert "configure_local_sqlite_connection(db)" in TARGET_RUNTIME


def test_discloud_diamond_layout_retires_standalone_watcher_apps() -> None:
    assert "RAM=1024" in DISCLOUD
    assert "VERSION=3.11" in DISCLOUD
    assert "SNIPERPLUG_LOCAL_SQLITE=true" in DEPLOY_DOC
    assert "SNIPERPLUG_EMBED_TARGET_WATCHER=true" in DEPLOY_DOC
    assert "SNIPERPLUG_EMBED_HP_WATCHER=false" in DEPLOY_DOC
    assert "TURSO_DATABASE_URL" in DEPLOY_DOC
    assert "separate Discloud applications" in DEPLOY_DOC
    assert "scripts/install_sqlite_snapshot.py" in DEPLOY_DOC
    assert "Discloud app backup" in DEPLOY_DOC
