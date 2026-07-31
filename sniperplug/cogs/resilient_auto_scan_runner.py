from __future__ import annotations

import asyncio
import time

import discord

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog as NativeAutoScanRunnerCog


MANUAL_PROGRESS_INTERVAL_SECONDS = 45


class AutoScanRunnerCog(NativeAutoScanRunnerCog):
    """Manual autoscan runner that never destroys completed work on a wall-clock timeout."""

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
