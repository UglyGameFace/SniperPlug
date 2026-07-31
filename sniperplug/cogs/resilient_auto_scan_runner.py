from __future__ import annotations

import asyncio
import platform
import sys
import time

import discord
from discord.ext import tasks

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog as NativeAutoScanRunnerCog


MANUAL_PROGRESS_INTERVAL_SECONDS = 45
SCHEDULE_LOOP_MINUTES = 30
SCHEDULED_QUERY_COUNT = 4
SCHEDULED_MIN_INTERVAL_SECONDS = 6 * 60 * 60
EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS = 5
EVENT_LOOP_LAG_WARNING_SECONDS = 2.0

_WALMART_SCHEDULE_LOCK = asyncio.Lock()
_NEXT_SCHEDULED_RUN_AT: dict[int, float] = {}


class AutoScanRunnerCog(NativeAutoScanRunnerCog):
    """Load-bounded Walmart autoscan runner.

    Manual scans remain responsive and keep completed work. Scheduled scans are
    intentionally much smaller, globally serialized, and protected by a six-hour
    safety floor even when a legacy row uses interval_hours=0 for "unlimited".
    """

    def __init__(self, bot):
        super().__init__(bot)
        self._event_loop_watchdog_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self.auto_scan_loop.start()
        self._event_loop_watchdog_task = asyncio.create_task(
            self._event_loop_watchdog(),
            name="sniperplug-event-loop-watchdog",
        )
        legacy.log.info(
            "Autoscan hardening active python=%s platform=%s scheduled_routes=%s "
            "scheduled_floor_hours=6 provider_concurrency=1",
            platform.python_version(),
            sys.platform,
            SCHEDULED_QUERY_COUNT,
        )

    async def cog_unload(self) -> None:
        self.auto_scan_loop.cancel()
        task = self._event_loop_watchdog_task
        self._event_loop_watchdog_task = None
        if task is not None:
            task.cancel()

    async def _run_autoscan_now_background(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        force: bool,
    ) -> None:
        lock = legacy.autoscan_lock(guild_id)
        async with lock:
            started = time.monotonic()
            try:
                target_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
                repair = await legacy.repair_public_alert_setup(
                    self.bot.db,
                    self.bot,
                    guild_id,
                    target_channel=target_channel,
                )
                config = repair.config if repair.config is not None else await legacy.get_public_alert_config(self.bot.db, guild_id)
                if repair.human_action_required:
                    await self._safe_autoscan_followup(
                        interaction,
                        "SniperPlug could not safely repair posting setup. " + repair.discord_line(),
                    )
                    return
                if not config.get("enabled") or not config.get("channel_id"):
                    await self._safe_autoscan_followup(
                        interaction,
                        "Public alerts are missing. Run `/autoscan_health` for the exact blocker.",
                    )
                    return
                if legacy.AUTO_SCAN_RETAILER not in set(config.get("retailers") or ()):
                    await self._safe_autoscan_followup(
                        interaction,
                        "Walmart is not enabled for public alerts in this server. Run `/autoscan_health`.",
                    )
                    return

                progress_task = asyncio.create_task(self._autoscan_progress_notice(interaction, started))
                try:
                    report = await self._run_guild_walmart_discovery(
                        legacy.AutoScanGuild(guild_id, config.get("channel_id")),
                        force=force,
                        query_count_override=8,
                        report_label="Manual broad pass" if force else "Manual pass",
                    )
                finally:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass

                self._log_all_report_warnings(report, source="manual")
                await self._send_autoscan_report(interaction, report, label="Manual pass result")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                legacy.log.exception("Manual /autoscan_now failed guild=%s", guild_id)
                await self._safe_autoscan_followup(
                    interaction,
                    f"Auto-scan hit an error after starting: `{legacy.clean_log_text(exc)}`",
                )

    async def _autoscan_progress_notice(
        self,
        interaction: discord.Interaction,
        started: float | None = None,
    ) -> None:
        started_at = started if started is not None else time.monotonic()
        notice_number = 0
        try:
            while True:
                await asyncio.sleep(MANUAL_PROGRESS_INTERVAL_SECONDS)
                notice_number += 1
                elapsed = max(1, int(time.monotonic() - started_at))
                await self._safe_autoscan_followup(
                    interaction,
                    "⏳ Walmart is still responding and the scan remains active. "
                    f"Elapsed: **{elapsed}s**. SniperPlug will keep completed route results and return a report instead of killing the whole pass. "
                    f"Progress update #{notice_number}.",
                )
        except asyncio.CancelledError:
            return
        except Exception:
            legacy.log.exception("Failed to send /autoscan_now progress notice")

    @tasks.loop(minutes=SCHEDULE_LOOP_MINUTES)
    async def auto_scan_loop(self) -> None:
        await self.bot.wait_until_ready()
        guilds = await legacy.list_public_alert_guilds(self.bot.db, bot=self.bot)
        if not guilds:
            return

        health_error = await legacy.provider_health_error_message()
        if health_error:
            legacy.log.info("Auto-scan skipped: %s", health_error)
            return

        # Intentionally sequential. A single Walmart provider plus a native
        # Turso/libSQL client should not receive multiple giant guild scans at once.
        for guild in guilds:
            await self._run_scheduled_guild(guild)

    async def _run_scheduled_guild(self, guild: legacy.AutoScanGuild, *_unused) -> None:
        gid = int(guild.guild_id)
        now = time.monotonic()
        due_at = float(_NEXT_SCHEDULED_RUN_AT.get(gid, 0.0))
        if now < due_at:
            legacy.log.debug(
                "Auto-scan safety floor blocked guild=%s retry_in_s=%s",
                gid,
                max(1, int(due_at - now)),
            )
            return

        guild_lock = legacy.autoscan_lock(gid)
        if guild_lock.locked():
            legacy.log.info("Auto-scan skipped guild=%s because another scan is already running", gid)
            return

        async with _WALMART_SCHEDULE_LOCK:
            async with guild_lock:
                started = time.monotonic()
                try:
                    report = await self._run_guild_walmart_discovery(
                        guild,
                        query_count_override=SCHEDULED_QUERY_COUNT,
                        report_label="Scheduled bounded pass",
                    )
                    self._log_all_report_warnings(report, source="scheduled")
                    elapsed = max(0.0, time.monotonic() - started)
                    legacy.log.info(
                        "Scheduled autoscan finished guild=%s elapsed_s=%.1f routes_cap=%s "
                        "checked=%s verified=%s posted=%s",
                        gid,
                        elapsed,
                        SCHEDULED_QUERY_COUNT,
                        report.products_checked,
                        report.total_cards,
                        report.public_result.posted,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    legacy.log.exception(
                        "Scheduled autoscan failed but other guilds remain isolated guild=%s",
                        gid,
                    )
                finally:
                    # Legacy interval_hours=0 used to mean unlimited and caused a
                    # 15-minute fire hose. The runtime safety floor always applies
                    # to background work; manual commands remain immediately usable.
                    _NEXT_SCHEDULED_RUN_AT[gid] = time.monotonic() + SCHEDULED_MIN_INTERVAL_SECONDS

    @auto_scan_loop.before_loop
    async def before_auto_scan_loop(self) -> None:
        await self.bot.wait_until_ready()

    def _log_all_report_warnings(self, report, *, source: str) -> None:
        for index, warning in enumerate(tuple(report.warnings or ()), start=1):
            legacy.log.warning(
                "Autoscan warning source=%s guild=%s warning=%s/%s detail=%s",
                source,
                report.guild_id,
                index,
                len(report.warnings),
                legacy.clean_log_text(warning),
            )

    async def _event_loop_watchdog(self) -> None:
        expected = time.monotonic() + EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS
        try:
            while True:
                await asyncio.sleep(EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS)
                now = time.monotonic()
                lag = max(0.0, now - expected)
                expected = now + EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS
                if lag >= EVENT_LOOP_LAG_WARNING_SECONDS:
                    legacy.log.warning(
                        "Event loop lag detected lag_s=%.2f threshold_s=%.2f "
                        "autoscan_global_busy=%s",
                        lag,
                        EVENT_LOOP_LAG_WARNING_SECONDS,
                        _WALMART_SCHEDULE_LOCK.locked(),
                    )
        except asyncio.CancelledError:
            return
