from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import math
import os
from typing import Any

from sniperplug.storage.db import Database
from sniperplug.storage.libsql_process import LibsqlProcessConnection


log = logging.getLogger("sniperplug")
MAX_EXACT_JSON_INTEGER = 2**53 - 1
DEFAULT_TURSO_OPERATION_TIMEOUT_SECONDS = 90.0


def libsql_safe_parameter(value: Any) -> Any:
    """Transport large integers as exact decimal text and reject rounded floats.

    Discord snowflakes fit SQLite's signed 64-bit INTEGER range but exceed the
    exact integer range of an IEEE-754 double. ``libsql 0.1.11`` silently rounds
    such bound integer parameters. SQLite INTEGER affinity converts an exact
    decimal string to int64 without losing digits.

    A large float is already irreversibly rounded before this boundary, so it is
    rejected instead of silently becoming a different guild, user, channel, or
    message ID.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) > MAX_EXACT_JSON_INTEGER:
        return str(value)
    if (
        isinstance(value, float)
        and math.isfinite(value)
        and abs(value) > MAX_EXACT_JSON_INTEGER
    ):
        raise ValueError(
            "Unsafe large floating-point database parameter; pass the exact ID "
            "as int or decimal text."
        )
    return value


class SnowflakeSafeLibsqlProcessConnection(LibsqlProcessConnection):
    async def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ):
        safe_params = None
        if params is not None:
            safe_params = tuple(libsql_safe_parameter(value) for value in params)
        return await super().execute(sql, safe_params)


class ProcessIsolatedDatabase(Database):
    """Use a child process for remote Turso while preserving Database methods."""

    async def connect(self) -> None:
        turso_url = (
            os.getenv("TURSO_DATABASE_URL", "").strip()
            or os.getenv("LIBSQL_URL", "").strip()
        )
        turso_token = (
            os.getenv("TURSO_AUTH_TOKEN", "").strip()
            or os.getenv("LIBSQL_AUTH_TOKEN", "").strip()
        )

        if not turso_url and not turso_token:
            await super().connect()
            return
        if not turso_url or not turso_token:
            raise RuntimeError(
                "Turso database config is incomplete. Set both "
                "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN."
            )
        if importlib.util.find_spec("libsql") is None:
            raise RuntimeError(
                "Turso database config is present, but Python package "
                "'libsql' is not installed."
            )

        operation_timeout = _positive_float_env(
            "TURSO_OPERATION_TIMEOUT_SECONDS",
            DEFAULT_TURSO_OPERATION_TIMEOUT_SECONDS,
        )
        connection = await SnowflakeSafeLibsqlProcessConnection.open(
            database=turso_url,
            auth_token=turso_token,
            operation_timeout_seconds=operation_timeout,
        )
        self.conn = connection
        self.backend = "turso-process-isolated"
        identity = connection.identity
        try:
            driver_version = importlib.metadata.version("libsql")
        except importlib.metadata.PackageNotFoundError:
            driver_version = "unknown"
        log.info(
            "Turso native driver isolated process=true worker_pid=%s "
            "parent_pid=%s native_libsql_in_gateway_process=false "
            "large_integer_text_transport=true transaction_replay=false "
            "driver_version=%s operation_timeout_s=%.1f",
            identity.pid if identity else "unknown",
            identity.parent_pid if identity else os.getpid(),
            driver_version,
            operation_timeout,
        )


def create_runtime_database(database_path: str) -> Database:
    """One database factory for the bot and every standalone watcher."""

    return ProcessIsolatedDatabase(database_path)


def _positive_float_env(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a positive finite number")
    return value
