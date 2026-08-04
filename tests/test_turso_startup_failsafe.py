from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from sniperplug.storage import process_database
from sniperplug.storage.libsql_embedded_replica import (
    EmbeddedReplicaLibsqlProcessConnection,
)


class _FakeRemoteConnection:
    def __init__(self, *, pid: int = 7001) -> None:
        self.identity = SimpleNamespace(pid=pid, parent_pid=os.getpid())
        self.worker_generation = 1
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _hard_exit_before_ready(pipe) -> None:
    try:
        pipe.close()
    finally:
        os._exit(23)


class _HardExitEmbeddedReplicaConnection(
    EmbeddedReplicaLibsqlProcessConnection
):
    def _start_worker_sync(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot start a closed process connection")
        self._discard_worker_sync()
        parent_pipe, child_pipe = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_hard_exit_before_ready,
            args=(child_pipe,),
            name="test-hard-exit-replica-worker",
            daemon=True,
        )
        process.start()
        child_pipe.close()
        self._parent_pipe = parent_pipe
        self._process = process


@pytest.fixture
def turso_environment(monkeypatch) -> None:
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://primary.example")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    monkeypatch.delenv("LIBSQL_URL", raising=False)
    monkeypatch.delenv("LIBSQL_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TURSO_EMBEDDED_REPLICA_ENABLED", raising=False)
    monkeypatch.setattr(
        process_database.importlib.util,
        "find_spec",
        lambda _name: object(),
    )


@pytest.mark.asyncio
async def test_remote_process_is_safe_default(
    monkeypatch,
    turso_environment,
) -> None:
    calls: list[dict] = []
    # Replica-only settings must be completely inert while the feature is off.
    monkeypatch.setenv("TURSO_REPLICA_SYNC_INTERVAL_SECONDS", "not-a-number")
    monkeypatch.setenv("TURSO_REPLICA_STARTUP_TIMEOUT_SECONDS", "also-invalid")
    monkeypatch.setenv("TURSO_REPLICA_PATH", "/path/that/must/not/be/touched")

    async def remote_open(cls, **kwargs):
        calls.append(dict(kwargs))
        return _FakeRemoteConnection()

    async def replica_must_not_open(cls, **_kwargs):
        raise AssertionError("embedded replica was attempted without opt-in")

    monkeypatch.setattr(
        process_database.SnowflakeSafeLibsqlProcessConnection,
        "open",
        classmethod(remote_open),
    )
    monkeypatch.setattr(
        process_database.SnowflakeSafeEmbeddedReplicaConnection,
        "open",
        classmethod(replica_must_not_open),
    )

    database = process_database.ProcessIsolatedDatabase(
        "./data/sniperplug.sqlite3"
    )
    await database.connect()

    assert database.backend == "turso-process-isolated"
    assert database.replica_mode == "remote-process"
    assert isinstance(database.conn, _FakeRemoteConnection)
    assert calls == [
        {
            "database": "libsql://primary.example",
            "auth_token": "test-token",
            "startup_timeout_seconds": 45.0,
            "operation_timeout_seconds": 90.0,
        }
    ]


@pytest.mark.asyncio
async def test_parent_falls_back_when_replica_startup_crashes(
    monkeypatch,
    turso_environment,
    caplog,
) -> None:
    monkeypatch.setenv("TURSO_EMBEDDED_REPLICA_ENABLED", "true")
    remote_calls: list[dict] = []

    async def replica_open(cls, **_kwargs):
        raise RuntimeError(
            "Turso embedded-replica worker exited before its startup response "
            "pid=91 exitcode=-6"
        )

    async def remote_open(cls, **kwargs):
        remote_calls.append(dict(kwargs))
        return _FakeRemoteConnection(pid=7002)

    monkeypatch.setattr(
        process_database.SnowflakeSafeEmbeddedReplicaConnection,
        "open",
        classmethod(replica_open),
    )
    monkeypatch.setattr(
        process_database.SnowflakeSafeLibsqlProcessConnection,
        "open",
        classmethod(remote_open),
    )

    database = process_database.ProcessIsolatedDatabase(
        "./data/sniperplug.sqlite3"
    )
    await database.connect()

    assert database.backend == "turso-process-isolated"
    assert database.replica_mode == "remote-process-fallback:RuntimeError"
    assert isinstance(database.conn, _FakeRemoteConnection)
    assert remote_calls
    assert "parent_remote_fallback=true" in caplog.text
    assert "stayed online through parent-level remote fallback" in caplog.text


@pytest.mark.asyncio
async def test_hard_child_exit_is_reported_with_pid_and_exit_code(
    tmp_path,
) -> None:
    started = time.monotonic()

    with pytest.raises(
        RuntimeError,
        match=r"exited before its startup response pid=\d+ exitcode=23",
    ):
        await _HardExitEmbeddedReplicaConnection.open(
            database=str(tmp_path / "hard-exit-replica.db"),
            sync_url="libsql://unused.example",
            auth_token="unused-token",
            startup_timeout_seconds=5.0,
            operation_timeout_seconds=10.0,
        )

    assert time.monotonic() - started < 4.0


def test_embedded_replica_flag_rejects_typos(
    monkeypatch,
    turso_environment,
) -> None:
    monkeypatch.setenv("TURSO_EMBEDDED_REPLICA_ENABLED", "probably")

    with pytest.raises(RuntimeError, match="must be one of true/false"):
        process_database._boolean_env(
            "TURSO_EMBEDDED_REPLICA_ENABLED",
            default=False,
        )
