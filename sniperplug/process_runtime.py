from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from sniperplug.bot import SniperPlugBot, env_enabled
from sniperplug.config import Settings
from sniperplug.hp_watcher.app import run as run_hp_watcher
from sniperplug.hp_watcher.config import HPWatcherSettings
from sniperplug.services.error_logging import (
    configure_runtime_logging,
    install_global_exception_hooks,
    shutdown_runtime_logging,
)
from sniperplug.services.gateway_runtime_monitor import monitor_gateway_loop_health
from sniperplug.services.local_sqlite_backup import run_local_sqlite_backup_loop
from sniperplug.storage.local_sqlite import configure_local_sqlite_connection
from sniperplug.storage.process_database import create_runtime_database
from sniperplug.target_watcher.app import run as run_target_watcher
from sniperplug.target_watcher.config import TargetWatcherSettings


log = logging.getLogger("sniperplug")
LOCAL_SQLITE_MODE_ENV = "SNIPERPLUG_LOCAL_SQLITE"
LOCAL_SQLITE_ALLOW_EMPTY_ENV = "SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY"
EMBED_HP_WATCHER_ENV = "SNIPERPLUG_EMBED_HP_WATCHER"
EMBED_TARGET_WATCHER_ENV = "SNIPERPLUG_EMBED_TARGET_WATCHER"
EMBEDDED_WATCHER_RESTART_SECONDS = 30.0
SQLITE_BACKUP_INTERVAL_HOURS_ENV = "SNIPERPLUG_SQLITE_BACKUP_INTERVAL_HOURS"
SQLITE_BACKUP_RETENTION_ENV = "SNIPERPLUG_SQLITE_BACKUP_RETENTION"
REMOTE_DATABASE_ENV_NAMES = (
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
    "LIBSQL_URL",
    "LIBSQL_AUTH_TOKEN",
)


class ProcessIsolatedSniperPlugBot(SniperPlugBot):
    """Canonical bot runtime with one authoritative production database file.

    Turso remains supported for backwards compatibility. When production runs on
    local SQLite, optional HP and Target workers execute inside this same
    Discloud application. Each worker opens its own SQLite connection to the
    same WAL-backed file, avoiding cross-coroutine transaction ownership while
    preserving one durable source of truth and one Discord publisher.
    """

    def __init__(self, settings: Settings):
        self._local_sqlite_mode = env_enabled(LOCAL_SQLITE_MODE_ENV, default=False)
        if self._local_sqlite_mode:
            _assert_remote_database_env_absent()
            _assert_local_database_present(settings.database_path)
        super().__init__(settings)
        # The base constructor does not connect, so replacement here creates no
        # abandoned connection and keeps every inherited service on one API.
        self.db = create_runtime_database(settings.database_path)
        self._runtime_database_closed = False
        self._gateway_health_task: asyncio.Task | None = None
        self._sqlite_backup_task: asyncio.Task | None = None
        self._embedded_watcher_tasks: dict[str, asyncio.Task] = {}

    async def setup_hook(self) -> None:
        await super().setup_hook()
        await configure_local_sqlite_connection(self.db)
        self._gateway_health_task = asyncio.create_task(
            monitor_gateway_loop_health(self),
            name="sniperplug-gateway-loop-correlation",
        )
        self._start_local_sqlite_services()

    def _start_local_sqlite_services(self) -> None:
        hp_enabled = env_enabled(EMBED_HP_WATCHER_ENV, default=False)
        target_enabled = env_enabled(EMBED_TARGET_WATCHER_ENV, default=False)
        backend = str(getattr(self.db, "backend", "unknown") or "unknown")

        if hp_enabled or target_enabled:
            if not self._local_sqlite_mode:
                raise RuntimeError(
                    "Embedded HP/Target watchers require SNIPERPLUG_LOCAL_SQLITE=true."
                )
            _assert_remote_database_env_absent()
            if backend != "sqlite":
                raise RuntimeError(
                    "Embedded HP/Target watchers require SniperPlug's local SQLite backend. "
                    "Remove TURSO_DATABASE_URL/TURSO_AUTH_TOKEN (and LIBSQL_URL/"
                    "LIBSQL_AUTH_TOKEN) before enabling embedded watchers."
                )

        if backend == "sqlite":
            interval_hours = _positive_float_env(SQLITE_BACKUP_INTERVAL_HOURS_ENV, 6.0)
            retention = _positive_int_env(SQLITE_BACKUP_RETENTION_ENV, 14)
            self._sqlite_backup_task = asyncio.create_task(
                run_local_sqlite_backup_loop(
                    self.db,
                    interval_seconds=interval_hours * 60.0 * 60.0,
                    retention=retention,
                ),
                name="sniperplug-local-sqlite-backup",
            )
            log.info(
                "Local SQLite production mode active path=%s explicit_mode=%s "
                "backup_interval_h=%.2f backup_retention=%s turso_required=false",
                self.settings.database_path,
                str(self._local_sqlite_mode).lower(),
                interval_hours,
                retention,
            )

        if hp_enabled:
            hp_settings = replace(
                HPWatcherSettings.from_env(),
                database_path=self.settings.database_path,
                require_remote_database=False,
                run_once=False,
            )
            _assert_remote_database_env_absent()
            hp_settings.validate_runtime()
            self._embedded_watcher_tasks["hp"] = asyncio.create_task(
                _supervise_embedded_watcher("hp", run_hp_watcher, hp_settings),
                name="sniperplug-embedded-hp-watcher",
            )

        if target_enabled:
            target_settings = replace(
                TargetWatcherSettings.from_env(),
                database_path=self.settings.database_path,
                require_remote_database=False,
                run_once=False,
            )
            _assert_remote_database_env_absent()
            target_settings.validate_runtime()
            self._embedded_watcher_tasks["target"] = asyncio.create_task(
                _supervise_embedded_watcher(
                    "target",
                    run_target_watcher,
                    target_settings,
                ),
                name="sniperplug-embedded-target-watcher",
            )

        log.info(
            "Embedded retailer watchers configured backend=%s hp=%s target=%s "
            "separate_discloud_watcher_apps_required=false",
            backend,
            str(hp_enabled).lower(),
            str(target_enabled).lower(),
        )

    async def close(self) -> None:
        """Stop embedded services and gateway diagnostics before closing the DB."""

        tasks_to_cancel = [
            *self._embedded_watcher_tasks.values(),
            self._sqlite_backup_task,
            self._gateway_health_task,
        ]
        self._embedded_watcher_tasks.clear()
        self._sqlite_backup_task = None
        self._gateway_health_task = None
        live_tasks = [task for task in tasks_to_cancel if task is not None]
        for task in live_tasks:
            task.cancel()
        if live_tasks:
            await asyncio.gather(*live_tasks, return_exceptions=True)

        try:
            await super().close()
        finally:
            if not self._runtime_database_closed:
                self._runtime_database_closed = True
                await self.db.close()


async def _supervise_embedded_watcher(
    name: str,
    runner: Callable[[Any], Awaitable[None]],
    settings: Any,
) -> None:
    """Contain one watcher failure without taking the Discord gateway offline."""

    while True:
        try:
            log.info(
                "Embedded %s watcher starting local_sqlite=true restart_supervision=true",
                name,
            )
            await runner(settings)
            log.error(
                "Embedded %s watcher returned unexpectedly; restarting in %.0fs",
                name,
                EMBEDDED_WATCHER_RESTART_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Embedded %s watcher failed; restarting in %.0fs",
                name,
                EMBEDDED_WATCHER_RESTART_SECONDS,
            )
        await asyncio.sleep(EMBEDDED_WATCHER_RESTART_SECONDS)


def _assert_local_database_present(database_path: str) -> None:
    """Refuse an accidental empty production cutover.

    Local SQLite is durable state, not a cache. When explicit local production
    mode is enabled, the migrated database must already exist before the normal
    SQLite connector gets a chance to create a fresh file. A deliberately fresh
    deployment requires a one-time explicit override.
    """

    configured = Path(str(database_path or "./data/sniperplug.sqlite3")).expanduser()
    path = configured if configured.is_absolute() else (Path.cwd() / configured).resolve()
    allow_empty = env_enabled(LOCAL_SQLITE_ALLOW_EMPTY_ENV, default=False)

    reason = ""
    if not path.exists():
        reason = "missing"
    elif not path.is_file():
        reason = "not-a-file"
    elif path.stat().st_size <= 0:
        reason = "empty"
    else:
        return

    if allow_empty:
        log.critical(
            "Local SQLite empty bootstrap explicitly allowed path=%s reason=%s "
            "override=%s; remove the override after the first successful boot",
            path,
            reason,
            LOCAL_SQLITE_ALLOW_EMPTY_ENV,
        )
        return

    raise RuntimeError(
        "SNIPERPLUG_LOCAL_SQLITE=true requires an existing non-empty database "
        f"file at {path} (state={reason}). Install the migrated production "
        "snapshot before startup. Only if intentionally starting from an empty "
        f"database, set {LOCAL_SQLITE_ALLOW_EMPTY_ENV}=true for the first boot "
        "and remove it immediately afterward."
    )


def _assert_remote_database_env_absent() -> None:
    present = [name for name in REMOTE_DATABASE_ENV_NAMES if os.getenv(name, "").strip()]
    if present:
        raise RuntimeError(
            "SNIPERPLUG_LOCAL_SQLITE=true cannot be combined with remote libSQL/Turso "
            "credentials. Remove: " + ", ".join(present)
        )


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(0.25, value)


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(1, min(100, value))


def run() -> None:
    settings = Settings.from_env()
    configure_runtime_logging()
    install_global_exception_hooks()
    if not settings.discord_token:
        shutdown_runtime_logging()
        raise RuntimeError("DISCORD_TOKEN is required")
    bot = ProcessIsolatedSniperPlugBot(settings)
    try:
        # SniperPlug already owns the root logging pipeline. Letting discord.py
        # install its default StreamHandler duplicates every Discord record and
        # puts a second synchronous stderr write back on the gateway process.
        bot.run(settings.discord_token, log_handler=None)
    finally:
        shutdown_runtime_logging()
