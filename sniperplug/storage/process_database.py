from __future__ import annotations

import importlib.util
import logging
import os
from typing import Any

from sniperplug.storage.db import Database
from sniperplug.storage.libsql_process import LibsqlProcessConnection


log = logging.getLogger("sniperplug")
MAX_EXACT_JSON_INTEGER = 2**53 - 1


def libsql_safe_parameter(value: Any) -> Any:
    """Transport large integers as decimal text so Hrana cannot round them.

    Discord snowflakes fit SQLite's signed 64-bit INTEGER range but exceed the
    exact integer range of an IEEE-754 double. ``libsql 0.1.11`` silently rounds
    such bound integer parameters. SQLite's INTEGER affinity converts an exact
    decimal string to int64 without losing digits, so this is safe for inserts,
    updates, and comparisons while ordinary counters remain numeric parameters.
    """

    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > MAX_EXACT_JSON_INTEGER
    ):
        return str(value)
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

        connection = await SnowflakeSafeLibsqlProcessConnection.open(
            database=turso_url,
            auth_token=turso_token,
        )
        self.conn = connection
        self.backend = "turso-process-isolated"
        identity = connection.identity
        log.info(
            "Turso native driver isolated process=true worker_pid=%s "
            "parent_pid=%s native_libsql_in_gateway_process=false "
            "large_integer_text_transport=true",
            identity.pid if identity else "unknown",
            identity.parent_pid if identity else os.getpid(),
        )
