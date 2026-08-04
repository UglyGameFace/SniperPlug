from __future__ import annotations

import asyncio
import logging
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
    primary and synchronizes remote changes on a bounded interval. The existing
    transaction-safe worker accepts explicit connection options; no library
    function is replaced or patched at runtime.

    Embedded-replica startup is optional. A native abort can close the startup
    pipe before Python sends a structured error, so this subclass converts that
    raw EOF into a diagnostic containing the child PID and exit code. The parent
    database factory can then fall back to the proven remote process safely.
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
        connection_options = {
            "sync_url": self.sync_url,
            "sync_interval": self.sync_interval_seconds,
            "initial_sync": True,
            "allow_remote_fallback": True,
            "fallback_database": self.sync_url,
        }
        process = self._context.Process(
            target=_libsql_worker_main,
            args=(
                child_pipe,
                self.database,
                self.auth_token,
                connection_options,
            ),
            name="sniperplug-libsql-replica-worker",
            daemon=True,
        )
        process.start()
        child_pipe.close()
        self._parent_pipe = parent_pipe
        self._process = process

    def _receive_startup_sync(self) -> dict[str, Any]:
        try:
            return super()._receive_startup_sync()
        except (BrokenPipeError, EOFError, OSError) as exc:
            process = self._process
            pid = process.pid if process is not None else "unknown"
            if process is not None:
                try:
                    process.join(timeout=0.5)
                except Exception:
                    pass
            exit_code = process.exitcode if process is not None else "unknown"
            raise RuntimeError(
                "Turso embedded-replica worker exited before its startup "
                f"response pid={pid} exitcode={exit_code}"
            ) from exc

    def _set_identity(self, ready: dict[str, Any]) -> None:
        previous_pid = self.identity.pid if self.identity else None
        super()._set_identity(ready)
        self.worker_generation += 1
        self.replica_mode = _connection_mode(ready)
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

    async def _request(self, operation: str, **payload: Any) -> dict[str, Any]:
        response = await super()._request(operation, **payload)
        mode = _connection_mode(response)
        if mode != "unknown" and mode != self.replica_mode:
            previous = self.replica_mode
            self.replica_mode = mode
            log.warning(
                "Turso database connection mode changed worker_pid=%s "
                "previous_mode=%s replica_mode=%s",
                self.identity.pid if self.identity else "unknown",
                previous,
                mode,
            )
        return response


def _connection_mode(response: dict[str, Any]) -> str:
    return str(response.get("connection_mode") or "unknown").strip() or "unknown"
