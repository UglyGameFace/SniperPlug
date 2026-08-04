from __future__ import annotations

from pathlib import Path

from sniperplug.storage.libsql_embedded_replica import (
    EmbeddedReplicaLibsqlProcessConnection,
    _read_mode_marker,
    _write_mode_marker,
)
from sniperplug.storage.libsql_process import LibsqlProcessConnection
from sniperplug.storage.process_database import (
    SnowflakeSafeEmbeddedReplicaConnection,
    SnowflakeSafeLibsqlProcessConnection,
    _replica_path_for,
)


ROOT = Path(__file__).resolve().parents[1]
REPLICA_RUNTIME = (
    ROOT / "sniperplug/storage/libsql_embedded_replica.py"
).read_text(encoding="utf-8")
PROCESS_DATABASE = (
    ROOT / "sniperplug/storage/process_database.py"
).read_text(encoding="utf-8")


def test_replica_path_is_separate_from_local_fallback_database(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("TURSO_REPLICA_PATH", raising=False)
    source = tmp_path / "sniperplug.sqlite3"

    replica = Path(_replica_path_for(source.as_posix()))

    assert replica == tmp_path / "sniperplug.turso-replica.sqlite3"
    assert replica != source
    assert replica.parent.exists()


def test_replica_path_env_override_is_respected(monkeypatch, tmp_path) -> None:
    override = tmp_path / "custom" / "replica.db"
    monkeypatch.setenv("TURSO_REPLICA_PATH", override.as_posix())

    assert Path(_replica_path_for("./data/sniperplug.sqlite3")) == override
    assert override.parent.exists()


def test_mode_marker_round_trip_never_contains_credentials(tmp_path) -> None:
    marker = tmp_path / "runtime-mode"

    _write_mode_marker(marker.as_posix(), "embedded-replica")

    assert _read_mode_marker(marker.as_posix()) == "embedded-replica"
    assert "token" not in marker.read_text(encoding="utf-8").lower()


def test_local_process_test_class_remains_distinct_from_production_replica() -> None:
    assert issubclass(SnowflakeSafeLibsqlProcessConnection, LibsqlProcessConnection)
    assert issubclass(
        SnowflakeSafeEmbeddedReplicaConnection,
        EmbeddedReplicaLibsqlProcessConnection,
    )
    assert SnowflakeSafeLibsqlProcessConnection is not SnowflakeSafeEmbeddedReplicaConnection


def test_embedded_replica_uses_supported_libsql_connection_options() -> None:
    for fragment in (
        "sync_url=sync_url",
        "sync_interval=max(1.0, float(sync_interval_seconds))",
        "offline=False",
        "auth_token=auth_token",
        "sync()",
        '"embedded-replica"',
        '"remote-fallback:',
    ):
        assert fragment in REPLICA_RUNTIME


def test_production_runtime_logs_mode_and_worker_generation() -> None:
    for fragment in (
        "embedded_replica_reads=%s",
        "replica_mode=%s",
        "worker_generation=%s",
        "bounded_queue_pressure=true",
        "no_op_cleanup_skips_write=true",
    ):
        assert fragment in PROCESS_DATABASE
    assert "TURSO_REPLICA_SYNC_INTERVAL_SECONDS" in PROCESS_DATABASE
    assert "TURSO_REPLICA_STARTUP_TIMEOUT_SECONDS" in PROCESS_DATABASE
    assert "TURSO_REPLICA_PATH" in PROCESS_DATABASE
    assert "TURSO_AUTH_TOKEN" not in REPLICA_RUNTIME.split("log.")[-1]
