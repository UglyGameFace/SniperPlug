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
SOURCE_ACCESS_BLOCK_BACKOFF_SECONDS = 6 * 60 * 60
TOTAL_FAILURE_LOG_INTERVAL_SECONDS = 15 * 60


class MovieTicketsCog(MultiSourceMovieTicketsCog, name="movies"):
    """Official free movie-ticket screenings, promotions, and codes."""

    # This registered implementation owns the `/movies` group. Source failures
    # preserve the last verified cache, let healthy sources continue, and avoid
    # repeating the same aiohttp traceback every 60 seconds.

    def __init__(self, bot):
        super().__init__(bot)
        self.store = SnowflakeSafeMovieTicketStore(bot.db)
        self._source_failure_counts: dict[str, int] = {}
        self._source_retry_after: dict[str, float] = {}
        self._source_access_blocked: set[str] = set()
        self._last_total_failure_log_monotonic = 0.0

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
            hard_blocked = source_key in self._source_access_blocked
            if remaining > 0 and (not manual_refresh or hard_blocked):
                reason = "official source access is cooling down" if hard_blocked else "transient source failure"
                errors.append(f"{label}: retry paused for {remaining}s because {reason}")
                continue

            attempted += 1
            try:
                outcome = await scanner(target_guild_id=target_guild_id)
            except Exception as error:  # noqa: BLE001 - healthy sources must continue.
                message = clean_text(str(error))[:300] or type(error).__name__
                errors.append(f"{label}: {message}")
                delay = self._register_source_failure(source_key, error)
                if _is_transient_source_error(error) or _is_source_access_block_error(error):
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

        source_state = await self.store.get_source_state(ATOM_SOURCE_KEY)
        if attempted == 0:
            # Every source is cooling down. Preserve cache and let the poll loop
            # continue quietly instead of manufacturing repeated failures.
            return MovieScanOutcome(
                modified=False,
                active_count=len(all_active),
                delivered_count=0,
                source_state=source_state,
            )

        failure_summary = " | ".join(errors)
        if target_guild_id is None:
            # Automatic monitoring is a cache-preserving condition watch. A total
            # upstream outage is degraded health, not an application traceback.
            now = time.monotonic()
            if (
                now - self._last_total_failure_log_monotonic
                >= TOTAL_FAILURE_LOG_INTERVAL_SECONDS
            ):
                log.warning(
                    "All official movie-ticket sources are temporarily unavailable; "
                    "preserved verified cache and entered backoff detail=%s",
                    failure_summary,
                )
                self._last_total_failure_log_monotonic = now
            return MovieScanOutcome(
                modified=False,
                active_count=len(all_active),
                delivered_count=0,
                source_state=source_state,
            )

        # Explicit guild refreshes still surface the complete upstream reason.
        raise RuntimeError("All official movie-ticket sources failed: " + failure_summary)

    def _source_backoff_remaining(self, source_key: str) -> int:
        retry_after = float(self._source_retry_after.get(source_key, 0.0) or 0.0)
        return max(0, math.ceil(retry_after - time.monotonic()))

    def _register_source_failure(self, source_key: str, error: Exception) -> int:
        failures = int(self._source_failure_counts.get(source_key, 0) or 0) + 1
        self._source_failure_counts[source_key] = failures
        if _is_source_access_block_error(error):
            delay = SOURCE_ACCESS_BLOCK_BACKOFF_SECONDS
            self._source_access_blocked.add(source_key)
        else:
            delay = SOURCE_BACKOFF_SECONDS[min(failures - 1, len(SOURCE_BACKOFF_SECONDS) - 1)]
        self._source_retry_after[source_key] = time.monotonic() + delay
        return delay

    def _clear_source_failure(self, source_key: str) -> None:
        self._source_failure_counts.pop(source_key, None)
        self._source_retry_after.pop(source_key, None)
        self._source_access_blocked.discard(source_key)


def _is_source_access_block_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "http 401",
            "http 403",
            "http 429",
            "access denied",
            "forbidden",
            "too many requests",
        )
    )


def _is_transient_source_error(error: Exception) -> bool:
    if isinstance(error, (asyncio.TimeoutError, aiohttp.ClientError, OSError)):
        return True
    if _is_source_access_block_error(error):
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
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )
