from __future__ import annotations

import asyncio
import logging
import math
import time

import aiohttp

from sniperplug.cogs.movie_tickets import MovieScanOutcome
from sniperplug.cogs.multi_source_movie_tickets import MovieTicketsCog as MultiSourceMovieTicketsCog
from sniperplug.services.fandango_movie_offers import FANDANGO_SOURCE_KEY
from sniperplug.services.gofobo_screenings import GOFOBO_SOURCE_KEY
from sniperplug.services.movie_ticket_drops import ATOM_SOURCE_KEY, clean_text
from sniperplug.services.movie_ticket_snowflake_store import SnowflakeSafeMovieTicketStore


log = logging.getLogger("sniperplug.movie_tickets.multi_source")
SOURCE_BACKOFF_SECONDS = (120, 300, 900, 1800)


class MovieTicketsCog(MultiSourceMovieTicketsCog, name="movies"):
    """Register the resilient multi-source implementation under `/movies`.

    Official sites occasionally time out. A single source failure must preserve
    the last verified cache, let the healthy sources continue, and avoid dumping
    the same aiohttp traceback every 60 seconds.
    """

    def __init__(self, bot):
        super().__init__(bot)
        self.store = SnowflakeSafeMovieTicketStore(bot.db)
        self._source_failure_counts: dict[str, int] = {}
        self._source_retry_after: dict[str, float] = {}

    async def _scan_official_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        outcomes: list[MovieScanOutcome] = []
        errors: list[str] = []
        attempted = 0
        manual_refresh = target_guild_id is not None
        source_scans = (
            ("Atom", ATOM_SOURCE_KEY, self._scan_atom_source),
            ("Fandango", FANDANGO_SOURCE_KEY, self._scan_fandango_source),
            ("Gofobo", GOFOBO_SOURCE_KEY, self._scan_gofobo_source),
        )

        for label, source_key, scanner in source_scans:
            remaining = self._source_backoff_remaining(source_key)
            if remaining > 0 and not manual_refresh:
                errors.append(f"{label}: retry paused for {remaining}s after a transient source failure")
                continue

            attempted += 1
            try:
                outcome = await scanner(target_guild_id=target_guild_id)
            except Exception as error:  # noqa: BLE001 - healthy sources must continue.
                message = clean_text(str(error))[:300] or type(error).__name__
                errors.append(f"{label}: {message}")
                delay = self._register_source_failure(source_key)
                if _is_transient_source_error(error):
                    log.warning(
                        "%s source temporarily unavailable; preserved verified cache and continued other sources retry_in_s=%s error=%s",
                        label,
                        delay,
                        message,
                    )
                else:
                    log.exception(
                        "%s source failed validation while multi-source movie scan continued retry_in_s=%s",
                        label,
                        delay,
                    )
                continue

            self._clear_source_failure(source_key)
            outcomes.append(outcome)

        all_active = await self.store.list_active_drops(limit=100)
        if outcomes:
            return MovieScanOutcome(
                modified=any(item.modified for item in outcomes),
                active_count=len(all_active),
                delivered_count=sum(item.delivered_count for item in outcomes),
                source_state=outcomes[0].source_state,
            )

        if attempted == 0:
            # Every source is cooling down. Preserve cache and let the poll loop
            # continue quietly instead of manufacturing repeated failures.
            source_state = await self.store.get_source_state(ATOM_SOURCE_KEY)
            return MovieScanOutcome(
                modified=False,
                active_count=len(all_active),
                delivered_count=0,
                source_state=source_state,
            )

        raise RuntimeError("All official movie-ticket sources failed: " + " | ".join(errors))

    def _source_backoff_remaining(self, source_key: str) -> int:
        retry_after = float(self._source_retry_after.get(source_key, 0.0) or 0.0)
        return max(0, math.ceil(retry_after - time.monotonic()))

    def _register_source_failure(self, source_key: str) -> int:
        failures = int(self._source_failure_counts.get(source_key, 0) or 0) + 1
        self._source_failure_counts[source_key] = failures
        delay = SOURCE_BACKOFF_SECONDS[min(failures - 1, len(SOURCE_BACKOFF_SECONDS) - 1)]
        self._source_retry_after[source_key] = time.monotonic() + delay
        return delay

    def _clear_source_failure(self, source_key: str) -> None:
        self._source_failure_counts.pop(source_key, None)
        self._source_retry_after.pop(source_key, None)


def _is_transient_source_error(error: Exception) -> bool:
    if isinstance(error, (asyncio.TimeoutError, aiohttp.ClientError, OSError)):
        return True
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "temporary failure",
            "server disconnected",
        )
    )
