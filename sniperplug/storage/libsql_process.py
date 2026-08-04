from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import sqlite3
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import Any

from sniperplug.storage.db import _LibsqlAsyncCursor


DEFAULT_STARTUP_TIMEOUT_SECONDS = 45.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 90.0
SLOW_OPERATION_WARNING_SECONDS = 2.0
log = logging.getLogger("sniperplug.database")


class LibsqlWorkerError(RuntimeError):
    """Raised when the isolated native libSQL worker reports an error."""


@dataclass(frozen=True)
class LibsqlProcessIdentity:
    pid: int
    parent_pid: int


class LibsqlProcessConnection:
    """Async DB-API facade backed by one dedicated native libSQL process.

    The parent event loop only waits on an OS pipe from a helper thread. One
    worker owns one connection and serializes all operations, preserving the
    existing transaction stream without letting native libSQL hold Discord's
    interpreter or gateway heartbeat.

    Stream recovery is deliberately transaction-aware. Read-only operations may
    reconnect and retry only when no transaction is active. Writes and commits
    are never replayed on a new connection because doing so could silently lose
    or duplicate part of an implicit transaction.
    """

    def __init__(
        self,
        *,
        database: str,
        auth_token: str = "",
        startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        self.database = str(database or "").strip()
        self.auth_token = str(auth_token or "").strip()
        self.startup_timeout_seconds = max(5.0, float(startup_timeout_seconds))
        self.operation_timeout_seconds = max(10.0, float(operation_timeout_seconds))
        self._lock = asyncio.Lock()
        self._context = multiprocessing.get_context("spawn")
        self._process: multiprocessing.Process | None = None
        self._parent_pipe: Any | None = None
        self._closed = False
        self.identity: LibsqlProcessIdentity | None = None

    @classmethod
    async def open(
        cls,
        *,
        database: str,
        auth_token: str = "",
        startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        operation_timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> "LibsqlProcessConnection":
        instance = cls(
            database=database,
            auth_token=auth_token,
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

    async def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> _LibsqlAsyncCursor:
        response = await self._request(
            "execute",
            sql=str(sql),
            params=None if params is None else tuple(params),
        )
        cursor = _LibsqlAsyncCursor(
            rows=list(response.get("rows") or []),
            columns=list(response.get("columns") or []),
        )
        cursor.rowcount = _normalize_rowcount(response.get("rowcount", -1))
        return cursor

    async def executescript(self, script: str) -> None:
        await self._request("executescript", script=str(script))

    async def commit(self) -> None:
        await self._request("commit")

    async def rollback(self) -> None:
        await self._request("rollback")

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                await asyncio.to_thread(
                    self._request_sync,
                    "close",
                    {},
                    min(15.0, self.operation_timeout_seconds),
                    False,
                )
            except Exception:
                pass
            finally:
                self._discard_worker_sync()

    async def _request(self, operation: str, **payload: Any) -> dict[str, Any]:
        wait_started = time.monotonic()
        async with self._lock:
            lock_wait = max(0.0, time.monotonic() - wait_started)
            if self._closed:
                raise RuntimeError("Turso/libSQL process connection is closed.")
            response = await asyncio.to_thread(
                self._request_sync,
                operation,
                payload,
                self.operation_timeout_seconds,
                True,
            )
            remote_elapsed = max(0.0, float(response.get("elapsed_seconds") or 0.0))
            if (
                lock_wait >= SLOW_OPERATION_WARNING_SECONDS
                or remote_elapsed >= SLOW_OPERATION_WARNING_SECONDS
            ):
                log.warning(
                    "Slow isolated database operation operation=%s label=%s "
                    "lock_wait_s=%.2f remote_s=%.2f worker_pid=%s",
                    operation,
                    _payload_label(operation, payload),
                    lock_wait,
                    remote_elapsed,
                    self.identity.pid if self.identity else "unknown",
                )
            return response

    def _request_sync(
        self,
        operation: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        allow_start: bool,
    ) -> dict[str, Any]:
        if allow_start:
            self._ensure_worker_sync()
        process = self._process
        pipe = self._parent_pipe
        if process is None or pipe is None or not process.is_alive():
            raise RuntimeError("Turso/libSQL worker process is not running.")

        request_id = uuid.uuid4().hex
        message = {
            "id": request_id,
            "operation": operation,
            "payload": payload,
        }
        try:
            pipe.send(message)
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._discard_worker_sync()
            raise RuntimeError("Could not send request to Turso/libSQL worker.") from exc

        timeout = max(1.0, float(timeout_seconds))
        if not pipe.poll(timeout):
            pid = process.pid
            self._discard_worker_sync()
            raise TimeoutError(
                f"Turso/libSQL worker pid={pid} exceeded {timeout:.1f}s for {operation}."
            )

        try:
            response = pipe.recv()
        except (BrokenPipeError, EOFError, OSError) as exc:
            self._discard_worker_sync()
            raise RuntimeError("Turso/libSQL worker closed its response pipe.") from exc

        if not isinstance(response, dict) or response.get("id") != request_id:
            self._discard_worker_sync()
            raise RuntimeError("Turso/libSQL worker returned an invalid response.")
        if not response.get("ok"):
            fatal = bool(response.get("fatal"))
            error_type = str(response.get("error_type") or "RuntimeError")
            error_text = str(response.get("error") or "Unknown libSQL worker error")
            remote_trace = str(response.get("traceback") or "").strip()
            detail = f"{error_type}: {error_text}"
            if remote_trace:
                detail += f"\nRemote worker traceback:\n{remote_trace}"
            if fatal:
                self._discard_worker_sync()
            raise LibsqlWorkerError(detail)
        return response

    def _start_and_receive_sync(self) -> dict[str, Any]:
        self._start_worker_sync()
        return self._receive_startup_sync()

    def _start_worker_sync(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot start a closed Turso/libSQL process connection.")
        if self._process is not None and self._process.is_alive():
            return
        self._discard_worker_sync()

        parent_pipe, child_pipe = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_libsql_worker_main,
            args=(child_pipe, self.database, self.auth_token),
            name="sniperplug-libsql-worker",
            daemon=True,
        )
        process.start()
        child_pipe.close()
        self._parent_pipe = parent_pipe
        self._process = process

    def _receive_startup_sync(self) -> dict[str, Any]:
        process = self._process
        pipe = self._parent_pipe
        if process is None or pipe is None:
            raise RuntimeError("Turso/libSQL worker was not started.")
        if not pipe.poll(self.startup_timeout_seconds):
            raise TimeoutError(
                "Turso/libSQL worker did not initialize within "
                f"{self.startup_timeout_seconds:.1f}s."
            )
        response = pipe.recv()
        if not isinstance(response, dict) or response.get("type") != "ready":
            raise RuntimeError("Turso/libSQL worker returned an invalid startup response.")
        if not response.get("ok"):
            raise RuntimeError(
                "Turso/libSQL worker failed to initialize: "
                f"{response.get('error_type', 'RuntimeError')}: "
                f"{response.get('error', 'unknown error')}"
            )
        return response

    def _ensure_worker_sync(self) -> None:
        process = self._process
        if process is not None and process.is_alive() and self._parent_pipe is not None:
            return
        ready = self._start_and_receive_sync()
        self._set_identity(ready)

    def _set_identity(self, ready: dict[str, Any]) -> None:
        self.identity = LibsqlProcessIdentity(
            pid=int(ready.get("pid") or 0),
            parent_pid=os.getpid(),
        )

    def _discard_worker_sync(self) -> None:
        pipe = self._parent_pipe
        process = self._process
        self._parent_pipe = None
        self._process = None

        if pipe is not None:
            try:
                pipe.close()
            except Exception:
                pass
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=3.0)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1.0)
            except Exception:
                pass


class _FatalWorkerError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Child process implementation
# ---------------------------------------------------------------------------


def _libsql_worker_main(
    pipe: Any,
    database: str,
    auth_token: str,
    connection_options: dict[str, Any] | None = None,
) -> None:
    connection: Any | None = None
    options = dict(connection_options or {})
    sync_url = str(options.get("sync_url") or "").strip()
    sync_interval = max(1.0, float(options.get("sync_interval") or 10.0))
    allow_remote_fallback = bool(options.get("allow_remote_fallback"))
    fallback_database = str(options.get("fallback_database") or sync_url).strip()
    connection_mode = "direct"

    def connect() -> Any:
        nonlocal connection_mode
        import libsql

        if sync_url:
            try:
                replica = libsql.connect(
                    database=database,
                    isolation_level="DEFERRED",
                    sync_url=sync_url,
                    sync_interval=sync_interval,
                    offline=False,
                    auth_token=auth_token,
                )
                sync = getattr(replica, "sync", None)
                if bool(options.get("initial_sync", True)) and callable(sync):
                    sync()
                connection_mode = "embedded-replica"
                return replica
            except Exception as replica_error:
                if not allow_remote_fallback or not fallback_database:
                    raise RuntimeError(
                        "Embedded replica initialization failed: "
                        f"{type(replica_error).__name__}"
                    ) from replica_error
                try:
                    fallback = libsql.connect(
                        database=fallback_database,
                        isolation_level="DEFERRED",
                        auth_token=auth_token,
                    )
                except Exception as remote_error:
                    raise RuntimeError(
                        "Embedded replica and remote Turso fallback both failed: "
                        f"replica={type(replica_error).__name__}; "
                        f"remote={type(remote_error).__name__}"
                    ) from remote_error
                connection_mode = (
                    f"remote-fallback:{type(replica_error).__name__}"
                )
                return fallback

        kwargs: dict[str, Any] = {
            "database": database,
            "isolation_level": "DEFERRED",
        }
        if auth_token:
            kwargs["auth_token"] = auth_token
        connection_mode = "direct"
        return libsql.connect(**kwargs)

    def reconnect() -> Any:
        nonlocal connection
        _close_sync(connection)
        connection = connect()
        _configure_connection(connection)
        return connection

    try:
        connection = connect()
        _configure_connection(connection)
        pipe.send(
            {
                "type": "ready",
                "ok": True,
                "pid": os.getpid(),
                "parent_pid": os.getppid(),
                "connection_mode": connection_mode,
            }
        )
    except BaseException as exc:
        try:
            pipe.send(
                {
                    "type": "ready",
                    "ok": False,
                    "pid": os.getpid(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "connection_mode": connection_mode,
                }
            )
        except Exception:
            pass
        _close_sync(connection)
        pipe.close()
        return

    try:
        while True:
            try:
                request = pipe.recv()
            except (EOFError, OSError):
                break
            if not isinstance(request, dict):
                continue

            request_id = str(request.get("id") or "")
            operation = str(request.get("operation") or "")
            payload = request.get("payload") or {}
            started = time.monotonic()
            fatal = False
            try:
                if operation == "execute":
                    sql = str(payload.get("sql") or "")
                    params = payload.get("params")
                    result = _execute_transaction_safe(
                        connection,
                        sql,
                        params,
                        reconnect,
                    )
                    columns, rows, rowcount = _consume_result(result)
                    response = {
                        "id": request_id,
                        "ok": True,
                        "columns": columns,
                        "rows": rows,
                        "rowcount": rowcount,
                    }
                elif operation == "executescript":
                    _execute_script_transaction_safe(
                        connection,
                        str(payload.get("script") or ""),
                        reconnect,
                    )
                    response = {"id": request_id, "ok": True}
                elif operation == "commit":
                    _commit_once(connection)
                    response = {"id": request_id, "ok": True}
                elif operation == "rollback":
                    _rollback_once(connection)
                    response = {"id": request_id, "ok": True}
                elif operation == "close":
                    _close_sync(connection)
                    connection = None
                    response = {
                        "id": request_id,
                        "ok": True,
                        "connection_mode": connection_mode,
                        "elapsed_seconds": max(
                            0.0,
                            time.monotonic() - started,
                        ),
                    }
                    pipe.send(response)
                    break
                elif operation == "test_sleep" and database == ":memory:" and not sync_url:
                    time.sleep(max(0.0, min(5.0, float(payload.get("seconds") or 0.0))))
                    response = {"id": request_id, "ok": True}
                else:
                    raise ValueError(f"Unsupported libSQL worker operation: {operation}")
            except _FatalWorkerError as exc:
                fatal = True
                response = _error_response(request_id, exc, fatal=True)
            except BaseException as exc:
                response = _error_response(request_id, exc, fatal=False)

            response["connection_mode"] = connection_mode
            response["elapsed_seconds"] = max(0.0, time.monotonic() - started)
            try:
                pipe.send(response)
            except (BrokenPipeError, EOFError, OSError):
                break
            if fatal:
                break
    finally:
        _close_sync(connection)
        try:
            pipe.close()
        except Exception:
            pass


def _configure_connection(connection: Any) -> None:
    try:
        connection.execute("PRAGMA foreign_keys=ON;")
        connection.commit()
    except Exception:
        pass


def _execute_transaction_safe(
    connection: Any,
    sql: str,
    params: Any,
    reconnect,
) -> Any:
    try:
        return _execute_sync(connection, sql, params)
    except Exception as exc:
        transaction_active = _in_transaction(connection)
        if (
            _is_retryable_libsql_stream_error(exc)
            and _is_read_only_sql(sql)
            and not transaction_active
        ):
            replacement = reconnect()
            return _execute_sync(replacement, sql, params)
        if transaction_active or _is_retryable_libsql_stream_error(exc):
            try:
                connection.rollback()
            except Exception:
                pass
            raise _FatalWorkerError(
                "libSQL operation failed inside or near a transaction; "
                "the worker was discarded instead of replaying writes: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        raise


def _execute_script_transaction_safe(
    connection: Any,
    script: str,
    reconnect,
) -> None:
    for statement in _split_sql_script(script):
        try:
            _execute_transaction_safe(connection, statement, None, reconnect)
        except BaseException:
            if _in_transaction(connection):
                try:
                    connection.rollback()
                except Exception as rollback_error:
                    raise _FatalWorkerError(
                        "SQL script failed and rollback also failed: "
                        f"{type(rollback_error).__name__}: {rollback_error}"
                    ) from rollback_error
            raise


def _commit_once(connection: Any) -> None:
    try:
        connection.commit()
    except Exception as exc:
        raise _FatalWorkerError(
            "libSQL commit outcome is unknown; commit was not replayed on a new "
            f"connection: {type(exc).__name__}: {exc}"
        ) from exc


def _rollback_once(connection: Any) -> None:
    try:
        connection.rollback()
    except Exception as exc:
        raise _FatalWorkerError(
            f"libSQL rollback failed: {type(exc).__name__}: {exc}"
        ) from exc


def _execute_sync(connection: Any, sql: str, params: Any) -> Any:
    if params is None:
        return connection.execute(sql)
    return connection.execute(sql, tuple(params))


def _consume_result(result: Any) -> tuple[list[str], list[Any], int]:
    if result is None:
        return [], [], -1
    columns = _extract_columns(result)
    rows: list[Any] = []
    fetchall = getattr(result, "fetchall", None)
    if callable(fetchall):
        try:
            raw_rows = list(fetchall())
        except Exception:
            raw_rows = []
    else:
        raw_rows = list(getattr(result, "rows", None) or [])
    for row in raw_rows:
        rows.append(_normalize_row(row, columns))
    rowcount = _normalize_rowcount(getattr(result, "rowcount", -1))
    return columns, rows, rowcount


def _normalize_rowcount(value: Any) -> int:
    if value is None:
        return -1
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return -1


def _extract_columns(result: Any) -> list[str]:
    description = getattr(result, "description", None)
    if description:
        return [
            str(item[0] if isinstance(item, (tuple, list)) and item else getattr(item, "name", item))
            for item in description
        ]
    columns = getattr(result, "columns", None)
    if callable(columns):
        columns = columns()
    return [str(column) for column in columns] if columns else []


def _normalize_row(row: Any, columns: list[str]) -> Any:
    if isinstance(row, dict):
        return row
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            return {str(key): row[key] for key in keys()}
        except Exception:
            pass
    if columns:
        try:
            return {
                columns[index]: row[index]
                for index in range(min(len(columns), len(row)))
            }
        except Exception:
            pass
    try:
        return tuple(row)
    except TypeError:
        return row


def _split_sql_script(script: str) -> tuple[str, ...]:
    """Split SQL with SQLite's parser, preserving triggers and quoted `;`."""

    statements: list[str] = []
    buffer: list[str] = []
    for char in str(script or ""):
        buffer.append(char)
        if char != ";":
            continue
        candidate = "".join(buffer).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            buffer = []
    remainder = "".join(buffer).strip()
    if remainder:
        statements.append(remainder)
    return tuple(statements)


def _is_read_only_sql(sql: str) -> bool:
    cleaned = str(sql or "").lstrip()
    while cleaned.startswith("--"):
        newline = cleaned.find("\n")
        if newline < 0:
            return True
        cleaned = cleaned[newline + 1 :].lstrip()
    token = cleaned.split(None, 1)[0].upper() if cleaned else ""
    return token in {"SELECT", "EXPLAIN"}


def _in_transaction(connection: Any) -> bool:
    try:
        return bool(getattr(connection, "in_transaction"))
    except Exception:
        return False


def _is_retryable_libsql_stream_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    retry_terms = (
        "stream not found",
        "stream already in use",
        "closed stream",
        "stream closed",
        "bad gateway",
        "status=502",
        "http 502",
        "connect to upstream failed",
        "option::unwrap",
        "called `option::unwrap()`",
        "called 'option::unwrap()'",
        "none value",
    )
    return "hrana" in text and any(term in text for term in retry_terms)


def _error_response(request_id: str, exc: BaseException, *, fatal: bool) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "fatal": bool(fatal),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )[-8000:],
    }


def _payload_label(operation: str, payload: dict[str, Any]) -> str:
    if operation != "execute":
        return operation
    sql = " ".join(str(payload.get("sql") or "").split())
    tokens = sql.split()
    return " ".join(tokens[:4])[:120] or "empty-sql"


def _close_sync(connection: Any | None) -> None:
    if connection is None:
        return
    close = getattr(connection, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass
