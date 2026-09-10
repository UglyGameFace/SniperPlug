from __future__ import annotations

import asyncio

from sniperplug.bot import SniperPlugBot
from sniperplug.config import Settings
from sniperplug.services.error_logging import (
    configure_runtime_logging,
    install_global_exception_hooks,
    shutdown_runtime_logging,
)
from sniperplug.services.gateway_runtime_monitor import monitor_gateway_loop_health
from sniperplug.storage.process_database import create_runtime_database


class ProcessIsolatedSniperPlugBot(SniperPlugBot):
    """Canonical bot runtime with native Turso work outside the gateway process."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        # The base constructor does not connect, so replacement here creates no
        # abandoned connection and keeps every inherited service on one API.
        self.db = create_runtime_database(settings.database_path)
        self._runtime_database_closed = False
        self._gateway_health_task: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        await super().setup_hook()
        self._gateway_health_task = asyncio.create_task(
            monitor_gateway_loop_health(self),
            name="sniperplug-gateway-loop-correlation",
        )

    async def close(self) -> None:
        """Stop gateway diagnostics/services, then close the DB worker once."""

        gateway_health_task = self._gateway_health_task
        self._gateway_health_task = None
        if gateway_health_task is not None:
            gateway_health_task.cancel()
            await asyncio.gather(gateway_health_task, return_exceptions=True)

        try:
            await super().close()
        finally:
            if not self._runtime_database_closed:
                self._runtime_database_closed = True
                await self.db.close()


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
