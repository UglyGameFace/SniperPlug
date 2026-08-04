from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from pathlib import Path
from typing import Any

from sniperplug.storage.libsql_process import (
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    LibsqlProcessConnection,
    _libsql_worker_main,
)


DEFAULT_REPLICA_STARTUP_TIMEOUT_SECONDS = 180.0
DEFAULT_REPLICA_SYNC_INTERVAL_SECONDS = 10.0
log = logging.getLogger("sniperplug.database")


class EmbeddedReplicaLibsqlProcessConnection(LibsqlProcessConnection):
    """Process-isolated libSQL connection backed by a local embedded replica.

    The native Python driver remains outside Discord's gateway process. Reads are
    served from the local replica while libSQL forwards writes to the Turso
    primary and synchronizes remote changes on a bounded interval. If the local
    replica cannot initialize, the child falls back to the existing remote-only
    connection and records that degraded mode explicitly instead of hiding it.
    """

    def __init__(
        self,
        *,
        database: str,
        sync_url: str,
        auth_token: str = "",
        sync_interval_seconds: float = DEFAULT_REPLICA_SYNC_INTERVAL_SECONDS,
        startup_timeout_seconds: float = DEFAULT_REPLICA_STARTUP_TIMEOUT_SECONDS,
        operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            database=database,
            auth_token=auth_token,
            startup_timeout_seconds=startup_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        self.sync_url = str(sync_url or "").strip()
        if not self.sync_url:
            raise ValueError("Embedded replica sync URL is required")
        self.sync_interval_seconds = max(1.0, float(sync_interval_seconds))
        self.replica_mode = "unknown"
        self.worker_generation = 0
        self._mode_marker_path = f"{self.database}.runtime-mode"

    @classmethod
    async def open(
        cls,
        *,
        database: str,
        sync_url: str,
        auth_token: str = "",
        sync_interval_seconds: float = DEFAULT_REPLICA_SYNC_INTERVAL_SECONDS,
        startup_timeout_seconds: float = DEFAULT_REPLICA_STARTUP_TIMEOUT_SECONDS,
        operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> "EmbeddedReplicaLibsqlProcessConnection":
        instance = cls(
            database=database,
            sync_url=sync_url,
            auth_token=auth_token,
            sync_interval_seconds=sync_interval_seconds,
            startup_timeout_seconds=startup_timeout_seconds,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        try:
            ready = await asyncio.to_thread(instance._start_and_receive_sync)
        except BaseException:
            instance._discard_worker_sync()
            raise
        instance._set_identity(ready)
        return instance

    def _start_worker_sync(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot start a closed Turso/libSQL process connection.")
        if self._process is not None and self._process.is_alive():
            return
        self._discard_worker_sync()

        replica = Path(self.database)
        replica.parent.mkdir(parents=True, exist_ok=True)
        parent_pipe, child_pipe = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_embedded_replica_worker_main,
            args=(
                child_pipe,
                self.database,
                self.sync_url,
                self.auth_token,
                self.sync_interval_seconds,
                self._mode_marker_path,
            ),
            name="sniperplug-libsql-replica-worker",
            daemon=True,
        )
        process.start()
        child_pipe.close()
        self._parent_pipe = parent_pipe
        self._process = process

    def _set_identity(self, ready: dict[str, Any]) -> None:
        previous_pid = self.identity.pid if self.identity else None
        super()._set_identity(ready)
        self.worker_generation += 1
        self.replica_mode = _read_mode_marker(self._mode_marker_path)
        fields = (
            self.worker_generation,
            self.identity.pid if self.identity else "unknown",
            previous_pid or "none",
            self.replica_mode,
            self.sync_interval_seconds,
        )
        if self.worker_generation == 1:
            log.info(
                "Turso database worker ready generation=%s worker_pid=%s "
                "previous_pid=%s replica_mode=%s sync_interval_s=%.1f",
                *fields,
            )
        else:
            log.warning(
                "Turso database worker restarted generation=%s worker_pid=%s "
                "previous_pid=%s replica_mode=%s sync_interval_s=%.1f",
                *fields,
            )


def _embedded_replica_worker_main(
    pipe: Any,
    replica_path: str,
    sync_url: str,
    auth_token: str,
    sync_interval_seconds: float,
    mode_marker_path: str,
) -> None:
    """Inject embedded-replica connection settings into the proven worker loop."""

    import libsql

    original_connect = libsql.connect

    def replica_connect(*_args: Any, **kwargs: Any) -> Any:
        isolation_level = kwargs.get("isolation_level", "DEFERRED")
        try:
            connection = original_connect(
                database=replica_path,
                isolation_level=isolation_level,
                sync_url=sync_url,
                sync_interval=max(1.0, float(sync_interval_seconds)),
                offline=False,
                auth_token=auth_token,
            )
            sync = getattr(connection, "sync", None)
            if callable(sync):
                sync()
            _write_mode_marker(mode_marker_path, "embedded-replica")
            return connection
        except BaseException as replica_error:
            try:
                connection = original_connect(
                    database=sync_url,
                    isolation_level=isolation_level,
                    auth_token=auth_token,
                )
            except BaseException as remote_error:
                raise RuntimeError(
                    "Embedded replica and remote Turso fallback both failed: "
                    f"replica={type(replica_error).__name__}; "
                    f"remote={type(remote_error).__name__}"
                ) from remote_error
            _write_mode_marker(
                mode_marker_path,
                f"remote-fallback:{type(replica_error).__name__}",
            )
            return connection

    libsql.connect = replica_connect
    try:
        _libsql_worker_main(pipe, replica_path, auth_token)
    finally:
        libsql.connect = original_connect


def _write_mode_marker(path: str, mode: str) -> None:
    try:
        marker = Path(path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(mode or "unknown"), encoding="utf-8")
    except Exception:
        pass


def _read_mode_marker(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip() or "unknown"
    except Exception:
        return "unknown"
