from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from sniperplug.storage.libsql_embedded_replica import (
    EmbeddedReplicaLibsqlProcessConnection,
    _connection_mode,
)
from sniperplug.storage.libsql_process import (
    LibsqlProcessConnection,
    _libsql_worker_main,
)
from sniperplug.storage.process_database import (
    SnowflakeSafeEmbeddedReplicaConnection,
    SnowflakeSafeLibsqlProcessConnection,
    _replica_path_for,
)


ROOT = Path(__file__).resolve().parents[1]
REPLICA_RUNTIME = (
    ROOT / "sniperplug/storage/libsql_embedded_replica.py"
).read_text(encoding="utf-8")
WORKER_RUNTIME = (
    ROOT / "sniperplug/storage/libsql_process.py"
).read_text(encoding="utf-8")
PROCESS_DATABASE = (
    ROOT / "sniperplug/storage/process_database.py"
).read_text(encoding="utf-8")


class FakeConnection:
    def __init__(self) -> None:
        self.sync_count = 0
        self.execute_calls: list[str] = []
        self.commit_count = 0
        self.closed = False
        self.in_transaction = False

    def sync(self) -> None:
        self.sync_count += 1

    def execute(self, sql, _params=()):
        self.execute_calls.append(str(sql))
        return None

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.in_transaction = False

    def close(self) -> None:
        self.closed = True


class FakePipe:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.requests = [
            {"id": "close-1", "operation": "close", "payload": {}},
        ]
        self.closed = False

    def send(self, payload) -> None:
        self.sent.append(dict(payload))

    def recv(self):
        return self.requests.pop(0)

    def close(self) -> None:
        self.closed = True


def fake_libsql_module(*, fail_replica: bool = False):
    module = ModuleType("libsql")
    calls: list[dict] = []
    connections: list[FakeConnection] = []

    def connect(**kwargs):
        calls.append(dict(kwargs))
        if fail_replica and "sync_url" in kwargs:
            raise RuntimeError("replica unavailable")
        connection = FakeConnection()
        connections.append(connection)
        return connection

    module.connect = connect
    module.calls = calls
    module.connections = connections
    return module


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


def test_connection_mode_comes_from_worker_response() -> None:
    assert _connection_mode({"connection_mode": "embedded-replica"}) == "embedded-replica"
    assert _connection_mode({"connection_mode": "remote-fallback:RuntimeError"}) == (
        "remote-fallback:RuntimeError"
    )
    assert _connection_mode({}) == "unknown"


def test_local_process_test_class_remains_distinct_from_production_replica() -> None:
    assert issubclass(SnowflakeSafeLibsqlProcessConnection, LibsqlProcessConnection)
    assert issubclass(
        SnowflakeSafeEmbeddedReplicaConnection,
        EmbeddedReplicaLibsqlProcessConnection,
    )
    assert SnowflakeSafeLibsqlProcessConnection is not SnowflakeSafeEmbeddedReplicaConnection


def test_worker_opens_and_syncs_embedded_replica(monkeypatch) -> None:
    fake_libsql = fake_libsql_module()
    monkeypatch.setitem(sys.modules, "libsql", fake_libsql)
    pipe = FakePipe()

    _libsql_worker_main(
        pipe,
        "/data/sniperplug-replica.db",
        "test-token",
        {
            "sync_url": "libsql://primary.example",
            "sync_interval": 10.0,
            "initial_sync": True,
            "allow_remote_fallback": True,
            "fallback_database": "libsql://primary.example",
        },
    )

    assert len(fake_libsql.calls) == 1
    call = fake_libsql.calls[0]
    assert call == {
        "database": "/data/sniperplug-replica.db",
        "isolation_level": "DEFERRED",
        "sync_url": "libsql://primary.example",
        "sync_interval": 10.0,
        "offline": False,
        "auth_token": "test-token",
    }
    assert fake_libsql.connections[0].sync_count == 1
    assert pipe.sent[0]["type"] == "ready"
    assert pipe.sent[0]["connection_mode"] == "embedded-replica"
    assert pipe.sent[-1]["id"] == "close-1"
    assert pipe.sent[-1]["connection_mode"] == "embedded-replica"
    assert pipe.closed is True


def test_worker_falls_back_to_remote_and_reports_degraded_mode(monkeypatch) -> None:
    fake_libsql = fake_libsql_module(fail_replica=True)
    monkeypatch.setitem(sys.modules, "libsql", fake_libsql)
    pipe = FakePipe()

    _libsql_worker_main(
        pipe,
        "/data/sniperplug-replica.db",
        "test-token",
        {
            "sync_url": "libsql://primary.example",
            "sync_interval": 10.0,
            "initial_sync": True,
            "allow_remote_fallback": True,
            "fallback_database": "libsql://primary.example",
        },
    )

    assert len(fake_libsql.calls) == 2
    assert "sync_url" in fake_libsql.calls[0]
    assert fake_libsql.calls[1] == {
        "database": "libsql://primary.example",
        "isolation_level": "DEFERRED",
        "auth_token": "test-token",
    }
    assert pipe.sent[0]["connection_mode"] == "remote-fallback:RuntimeError"
    assert pipe.sent[-1]["connection_mode"] == "remote-fallback:RuntimeError"


def test_embedded_replica_uses_explicit_supported_worker_options() -> None:
    for fragment in (
        '"sync_url": self.sync_url',
        '"sync_interval": self.sync_interval_seconds',
        '"initial_sync": True',
        '"allow_remote_fallback": True',
        "target=_libsql_worker_main",
    ):
        assert fragment in REPLICA_RUNTIME

    for fragment in (
        "sync_url=sync_url",
        "sync_interval=sync_interval",
        "offline=False",
        "auth_token=auth_token",
        "sync()",
        'connection_mode = "embedded-replica"',
        'f"remote-fallback:{type(replica_error).__name__}"',
    ):
        assert fragment in WORKER_RUNTIME


def test_replica_runtime_never_monkeypatches_libsql() -> None:
    assert "libsql.connect =" not in REPLICA_RUNTIME
    assert "original_connect" not in REPLICA_RUNTIME
    assert "monkeypatch" not in REPLICA_RUNTIME.lower()
    assert "connection_options" in REPLICA_RUNTIME
    assert 'response["connection_mode"]' in WORKER_RUNTIME


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
    assert "auth_token" not in " ".join(
        line for line in REPLICA_RUNTIME.splitlines() if "log." in line
    )
