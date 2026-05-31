from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

from sniperplug.services.error_logging import make_error_id, record_exception_event, send_error_notice


log = logging.getLogger("sniperplug.errors")
_PATCH_ATTR = "_sniperplug_error_bridge_installed"


def install_local_command_error_bridges(bot: commands.Bot) -> None:
    """Route older per-cog error helper paths into the central error logger.

    Some cogs have command-specific `.error` handlers that catch exceptions before
    the global app-command error hook can see them. This bridge replaces those
    helper functions so handled local errors still get an Error ID and DB record.
    """
    try:
        from sniperplug.cogs import deal_scanner
    except Exception as exc:
        log.warning("Could not install deal_scanner error bridge: %s", exc)
        return

    current = getattr(deal_scanner, "send_command_error", None)
    if current is None or getattr(current, _PATCH_ATTR, False):
        return

    async def logged_send_command_error(interaction: discord.Interaction, message: str) -> None:
        error_id = make_error_id("CMD")
        error = RuntimeError(message)
        context = {
            "command": getattr(getattr(interaction, "command", None), "qualified_name", None),
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "user_id": getattr(interaction.user, "id", None),
            "local_handler_message": message,
            "source": "deal_scanner.send_command_error",
        }
        log.error(
            "Local command error captured error_id=%s command=%s guild=%s user=%s message=%s",
            error_id,
            context.get("command"),
            context.get("guild_id"),
            context.get("user_id"),
            message,
        )
        await record_exception_event(getattr(bot, "db", None), source="local_command_error", error=error, error_id=error_id, context=context)
        await send_error_notice(interaction, error_id, extra="The old local handler caught this, so I saved the message and context for debugging.")

    setattr(logged_send_command_error, _PATCH_ATTR, True)
    setattr(deal_scanner, "send_command_error", logged_send_command_error)
    log.info("Installed local command error bridge for deal_scanner.")
