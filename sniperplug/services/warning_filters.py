from __future__ import annotations

import logging
import warnings


class KnownNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        known_noise = (
            "PyNaCl is not installed, voice will NOT be supported",
            "davey is not installed, voice will NOT be supported",
            "Privileged message content intent is missing",
            "'asyncio.iscoroutinefunction' is deprecated",
        )
        return not any(text in message for text in known_noise)


def install_warning_filters() -> None:
    """Hide known third-party library noise that SniperPlug cannot fix locally."""
    warnings.filterwarnings(
        action="ignore",
        message=r".*asyncio\.iscoroutinefunction.*deprecated.*",
        category=DeprecationWarning,
    )
    noise_filter = KnownNoiseFilter()
    for logger_name in ("discord.client", "discord.ext.commands.bot", "py.warnings"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(existing, KnownNoiseFilter) for existing in logger.filters):
            logger.addFilter(noise_filter)
