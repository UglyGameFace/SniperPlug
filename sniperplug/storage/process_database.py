from __future__ import annotations

import importlib.util
import logging
import os

from sniperplug.storage.db import Database
from sniperplug.storage.libsql_process import LibsqlProcessConnection


log = logging.getLogger("sniperplug")


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

        connection = await LibsqlProcessConnection.open(
            database=turso_url,
            auth_token=turso_token,
        )
        self.conn = connection
        self.backend = "turso-process-isolated"
        identity = connection.identity
        log.info(
            "Turso native driver isolated process=true worker_pid=%s "
            "parent_pid=%s native_libsql_in_gateway_process=false",
            identity.pid if identity else "unknown",
            identity.parent_pid if identity else os.getpid(),
        )
