from __future__ import annotations

from sniperplug.bot import SniperPlugBot
from sniperplug.config import Settings
from sniperplug.services.error_logging import (
    configure_runtime_logging,
    install_global_exception_hooks,
)
from sniperplug.storage.process_database import create_runtime_database


class ProcessIsolatedSniperPlugBot(SniperPlugBot):
    """Canonical bot runtime with native Turso work outside the gateway process."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        # The base constructor does not connect, so replacement here creates no
        # abandoned connection and keeps every inherited service on one API.
        self.db = create_runtime_database(settings.database_path)
        self._runtime_database_closed = False

    async def close(self) -> None:
        """Unload Discord services first, then stop the DB worker exactly once."""

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
        raise RuntimeError("DISCORD_TOKEN is required")
    bot = ProcessIsolatedSniperPlugBot(settings)
    bot.run(settings.discord_token)
