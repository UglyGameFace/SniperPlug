"""Home Depot penny tracker cog: owns the polling loop's lifecycle.

The loop lives here; all state and logic live in
:mod:`sniperplug.services.hd_penny_tracker`. The cycle is exception-isolated
and credit-frugal (see the service): tracked items only, final-stage items
first, a persisted daily poll cap. When SERPAPI_API_KEY is absent the cycle
skips quietly and the /menu status page says tracking needs the key — no
crashes, no fake results.
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import commands

from sniperplug.services.hd_penny_tracker import (
    HdPennyConfig,
    ensure_hd_penny_tables,
    run_hd_penny_poll_cycle,
)
from sniperplug.services.hd_standing_hunts import (
    ensure_hd_hunts_table,
    run_hd_hunt_discovery_cycle,
)


log = logging.getLogger("sniperplug")

_INITIAL_DELAY_SECONDS = 180.0


class HomeDepotPennyTrackerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        try:
            await ensure_hd_penny_tables(self.bot.db)
            await ensure_hd_hunts_table(self.bot.db)
        except Exception:
            log.exception("Home Depot penny tracker table setup failed; loop still starting")
        self._task = asyncio.create_task(
            self._run_forever(), name="sniperplug-hd-penny-tracker"
        )

    async def cog_unload(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run_forever(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY_SECONDS)
        while True:
            config = HdPennyConfig.from_env()
            try:
                summary = await run_hd_penny_poll_cycle(self.bot.db, config=config)
                if summary.polled or summary.events_published or summary.failures:
                    log.info(summary.log_line())
                elif summary.skipped_reason:
                    log.info(summary.log_line())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Home Depot penny poll cycle failed; loop continues")
            # Standing-hunt discovery rides the same loop (no second
            # task): it re-runs due hunts through the quota guard and
            # seeds new candidates into the tracker the poll above
            # watches. Due-ness is persisted per hunt (daily default),
            # so most passes are a cheap no-op.
            try:
                hunt_summary = await run_hd_hunt_discovery_cycle(self.bot.db)
                if hunt_summary.ran or hunt_summary.quota_skips or hunt_summary.failures:
                    log.info(hunt_summary.log_line())
                elif hunt_summary.skipped_reason:
                    log.info(hunt_summary.log_line())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Home Depot standing-hunt discovery cycle failed; loop continues")
            await asyncio.sleep(max(3600.0, config.poll_interval_hours * 3600.0))
