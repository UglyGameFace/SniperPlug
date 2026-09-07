from __future__ import annotations

import asyncio
import logging
import math
import time

import aiohttp
import discord
from discord import app_commands

from sniperplug.cogs.movie_tickets import MovieScanOutcome
from sniperplug.cogs.multi_source_movie_tickets import MovieTicketsCog as MultiSourceMovieTicketsCog
from sniperplug.services.fandango_movie_offers import (
    FANDANGO_OFFERS_URL,
    FANDANGO_SOURCE_KEY,
)
from sniperplug.services.gofobo_screenings import GOFOBO_HOME_URL, GOFOBO_SOURCE_KEY
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    ATOM_SOURCE_KEY,
    AtomPromotionsClient,
    MovieTicketConfig,
    MovieTicketDrop,
    MovieTicketSourceState,
    clean_text,
)
from sniperplug.services.movie_ticket_recovery import (
    movie_source_health_text,
    parse_current_atom_promotions_html,
)
from sniperplug.services.movie_ticket_snowflake_store import SnowflakeSafeMovieTicketStore


log = logging.getLogger("sniperplug.movie_tickets.multi_source")
SOURCE_BACKOFF_SECONDS = (120, 300, 900, 1800)
SOURCE_ACCESS_BLOCK_BACKOFF_SECONDS = 6 * 60 * 60
TOTAL_FAILURE_LOG_INTERVAL_SECONDS = 15 * 60


class MovieTicketsCog(MultiSourceMovieTicketsCog, name="movies"):
    """Official free movie-ticket screenings, promotions, and codes."""

    # This registered implementation owns the `/movies` group. Source failures
    # keep durable history, let healthy sources continue, hide stale public
    # results, and avoid repeating the same aiohttp traceback every 60 seconds.

    def __init__(self, bot):
        super().__init__(bot)
        self.store = SnowflakeSafeMovieTicketStore(bot.db)
        self._source_failure_counts: dict[str, int] = {}
        self._source_retry_after: dict[str, float] = {}
        self._source_access_blocked: set[str] = set()
        self._last_total_failure_log_monotonic = 0.0
        self._last_source_scan_errors: tuple[str, ...] = ()

    @app_commands.command(
        name="setup",
        description="Enable official free movie-ticket and screening alerts in a channel.",
    )
    @app_commands.describe(alert_channel="Channel where verified movie-ticket alerts should post.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        alert_channel: discord.TextChannel,
    ) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        missing = self._missing_bot_permissions(interaction.guild, alert_channel)
        if missing:
            await interaction.followup.send(
                self._missing_permissions_message(alert_channel, missing),
                ephemeral=True,
            )
            return

        await self.store.save_config(
            MovieTicketConfig(
                guild_id=int(interaction.guild_id),
                alert_channel_id=int(alert_channel.id),
                enabled=True,
            )
        )

        try:
            outcome = await self._scan_official_source(target_guild_id=int(interaction.guild_id))
            scan_line = (
                f"Recently confirmed offers/screenings: **{outcome.active_count}**. "
                f"New alerts posted here: **{outcome.delivered_count}**."
            )
            warning = self._source_warning_summary()
            if warning:
                scan_line += f"\n⚠️ {warning}"
        except Exception as error:
            log.exception("Multi-source movie setup refresh failed guild=%s", interaction.guild_id)
            scan_line = (
                "The destination was saved, but no official source could be confirmed live right now: "
                f"`{clean_text(str(error))[:400]}`. Automatic monitoring will retry on its own."
            )

        await interaction.followup.send(
            f"🎬 Movie-ticket alerts are **enabled** in {alert_channel.mention}.\n{scan_line}\n"
            "Setup controls where alerts go; rerunning setup cannot bypass an official site's 403/429/access block. "
            "Stale cached offers are hidden until their source validates again.",
            ephemeral=True,
        )

    @app_commands.command(
        name="latest",
        description="Refresh and show recently confirmed official ticket offers and screenings.",
    )
    async def latest(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        refresh_error = ""
        try:
            await self._scan_official_source(
                target_guild_id=int(interaction.guild_id) if interaction.guild_id else None
            )
        except Exception as error:
            refresh_error = clean_text(str(error))[:500]
            log.warning(
                "Manual movie latest refresh could not confirm any source guild=%s error=%s",
                interaction.guild_id,
                refresh_error,
            )

        drops = await self.store.list_active_drops(limit=8)
        warning = self._source_warning_summary()
        if not drops:
            message = (
                "No **recently confirmed** public movie-ticket offers or screening opportunities are available from the connected official sources right now."
            )
            if refresh_error:
                message += f"\nLive refresh: `{refresh_error}`"
            elif warning:
                message += f"\n⚠️ {warning}"
            message += "\nOld cached offers are intentionally hidden instead of being presented as current."
            await interaction.followup.send(message, ephemeral=True)
            return

        content = (
            f"Found **{len(drops)}** recently confirmed official movie-ticket offer(s) or screening lead(s). "
            "Claim immediately; codes, passes, and local inventory can disappear early."
        )
        if refresh_error:
            content += f"\n⚠️ Some live sources could not be confirmed: `{refresh_error}`"
        elif warning:
            content += f"\n⚠️ {warning}"
        await interaction.followup.send(
            content=content,
            embeds=await self._build_drop_embeds(drops),
            ephemeral=True,
        )

    @app_commands.command(name="scan", description="Check every connected official movie-ticket source now.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            outcome = await self._scan_official_source(target_guild_id=int(interaction.guild_id))
        except Exception as error:
            message = clean_text(str(error))[:500]
            log.warning(
                "Manual movie scan could not confirm any source guild=%s error=%s",
                interaction.guild_id,
                message,
            )
            await interaction.followup.send(
                f"No connected official source could be confirmed live: `{message}`. "
                "Durable history was preserved, but stale offers are hidden from current results. "
                "SniperPlug will retry automatically; rerunning setup does not bypass an upstream access block.",
                ephemeral=True,
            )
            return

        content = (
            f"Official multi-source scan complete. Any source changed: **{'yes' if outcome.modified else 'no'}** • "
            f"Recently confirmed offers/screenings: **{outcome.active_count}** • "
            f"New alerts delivered to this server: **{outcome.delivered_count}**."
        )
        warning = self._source_warning_summary()
        if warning:
            content += f"\n⚠️ {warning}"
        await interaction.followup.send(content, ephemeral=True)

    async def _scan_official_source(
        self,
        *,
        target_guild_id: int | None = None,
    ) -> MovieScanOutcome:
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
                reason = (
                    "official source access is cooling down"
                    if hard_blocked
                    else "transient source failure"
                )
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
                        "%s source temporarily unavailable; durable cache retained and other sources continued retry_in_s=%s error=%s",
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

        self._last_source_scan_errors = tuple(errors)
        all_active = await self.store.list_active_drops(limit=100)
        if outcomes:
            return MovieScanOutcome(
                modified=any(item.modified for item in outcomes),
                active_count=len(all_active),
                delivered_count=sum(item.delivered_count for item in outcomes),
                source_state=outcomes[0].source_state,
            )

        source_state = await self.store.get_source_state(ATOM_SOURCE_KEY)
        failure_summary = " | ".join(errors)
        if attempted == 0:
            if manual_refresh:
                raise RuntimeError(
                    "All official movie-ticket sources are cooling down: " + failure_summary
                )
            return MovieScanOutcome(
                modified=False,
                active_count=len(all_active),
                delivered_count=0,
                source_state=source_state,
            )

        if target_guild_id is None:
            now = time.monotonic()
            if (
                now - self._last_total_failure_log_monotonic
                >= TOTAL_FAILURE_LOG_INTERVAL_SECONDS
            ):
                log.warning(
                    "All official movie-ticket sources are temporarily unavailable; durable history retained and stale public cache hidden after freshness window detail=%s",
                    failure_summary,
                )
                self._last_total_failure_log_monotonic = now
            return MovieScanOutcome(
                modified=False,
                active_count=len(all_active),
                delivered_count=0,
                source_state=source_state,
            )

        raise RuntimeError("All official movie-ticket sources failed: " + failure_summary)

    async def _scan_atom_source(
        self,
        *,
        target_guild_id: int | None = None,
    ) -> MovieScanOutcome:
        """Use the current Atom layout guard while keeping normal source semantics."""

        async with self._source_lock:
            state = await self.store.get_source_state(ATOM_SOURCE_KEY)
            try:
                fetched = await AtomPromotionsClient(self._require_session()).fetch(state)
                modified = not fetched.not_modified
                if fetched.not_modified:
                    drops = await self._list_source_drops(ATOM_SOURCE_KEY)
                else:
                    parsed = parse_current_atom_promotions_html(fetched.html)
                    if not parsed.document_valid:
                        raise RuntimeError(
                            "The official Atom promotions page structure could not be verified, so SniperPlug retained durable history without presenting it as newly confirmed."
                        )
                    await self.store.replace_active_drops(ATOM_SOURCE_KEY, parsed.drops)
                    drops = list(parsed.drops)

                await self.store.record_source_success(
                    source_key=ATOM_SOURCE_KEY,
                    etag=fetched.etag or state.etag,
                    last_modified=fetched.last_modified or state.last_modified,
                    active_drop_count=len(drops),
                )
                delivered = await self._deliver_drops(
                    drops,
                    target_guild_id=target_guild_id,
                )
                return MovieScanOutcome(
                    modified,
                    len(drops),
                    delivered,
                    await self.store.get_source_state(ATOM_SOURCE_KEY),
                )
            except Exception as error:
                await self.store.record_source_error(ATOM_SOURCE_KEY, str(error))
                raise

    async def _list_source_drops(self, source_key: str) -> list[MovieTicketDrop]:
        # HTTP 304 is a fresh server confirmation that the prior representation is
        # unchanged. It must be able to read durable rows even if their previous
        # success timestamp aged beyond the public serving window. The caller
        # records a new source success immediately after this read.
        return [
            drop
            for drop in await self.store.list_active_drops(
                limit=250,
                include_stale=True,
            )
            if drop.source_key == source_key
        ]

    def _source_health_lines(
        self,
        states: dict[str, MovieTicketSourceState],
    ) -> list[str]:
        return [
            movie_source_health_text(
                "Atom",
                states[ATOM_SOURCE_KEY],
                official_url=ATOM_PROMOTIONS_URL,
            ),
            movie_source_health_text(
                "Fandango",
                states[FANDANGO_SOURCE_KEY],
                official_url=FANDANGO_OFFERS_URL,
            ),
            movie_source_health_text(
                "Gofobo",
                states[GOFOBO_SOURCE_KEY],
                official_url=GOFOBO_HOME_URL,
            ),
        ]

    def _source_warning_summary(self) -> str:
        if not self._last_source_scan_errors:
            return ""
        detail = " | ".join(self._last_source_scan_errors)
        return f"Some official sources are degraded; only recently confirmed results are shown. {detail[:700]}"

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
