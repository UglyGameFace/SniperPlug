from __future__ import annotations

from sniperplug.bot import SniperPlugBot
from sniperplug.config import Settings
from sniperplug.services.error_logging import (
    configure_runtime_logging,
    install_global_exception_hooks,
)
from sniperplug.storage.process_database import ProcessIsolatedDatabase


class ProcessIsolatedSniperPlugBot(SniperPlugBot):
    """Canonical bot runtime with native Turso work outside the gateway process."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        # The base constructor does not connect, so replacing the database here
        # creates no abandoned connection and leaves every inherited service on
        # the same Database API.
        self.db = ProcessIsolatedDatabase(settings.database_path)


def run() -> None:
    settings = Settings.from_env()
    configure_runtime_logging()
    install_global_exception_hooks()
    if not settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required")
    bot = ProcessIsolatedSniperPlugBot(settings)
    bot.run(settings.discord_token)
