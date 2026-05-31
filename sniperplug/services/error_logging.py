from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import traceback
import uuid
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


ERROR_LOGGER_NAME = "sniperplug.errors"
MAX_TRACEBACK_CHARS = 12000
MAX_CONTEXT_CHARS = 6000


error_log = logging.getLogger(ERROR_LOGGER_NAME)


def configure_runtime_logging() -> None:
    """Install console + rotating file logging once.

    Env knobs:
    - SNIPERPLUG_LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
    - SNIPERPLUG_LOG_FILE=./data/logs/sniperplug.log
    - SNIPERPLUG_LOG_MAX_BYTES=5242880
    - SNIPERPLUG_LOG_BACKUPS=5
    """
    level_name = os.getenv("SNIPERPLUG_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    log_file = Path(os.getenv("SNIPERPLUG_LOG_FILE", "./data/logs/sniperplug.log").strip() or "./data/logs/sniperplug.log")
    max_bytes = _env_int("SNIPERPLUG_LOG_MAX_BYTES", 5 * 1024 * 1024)
    backups = _env_int("SNIPERPLUG_LOG_BACKUPS", 5)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(level)
        root.addHandler(console)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == str(log_file.resolve()) for handler in root.handlers):
            file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            root.addHandler(file_handler)
    except Exception as exc:
        logging.getLogger(ERROR_LOGGER_NAME).warning("Could not enable rotating file logs: %s", exc)

    logging.captureWarnings(True)
    warnings.simplefilter("default")


def install_global_exception_hooks() -> None:
    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        error_id = make_error_id("UNHANDLED")
        error_log.critical(
            "Unhandled process exception error_id=%s type=%s message=%s\n%s",
            error_id,
            exc_type.__name__,
            exc,
            "".join(traceback.format_exception(exc_type, exc, tb))[:MAX_TRACEBACK_CHARS],
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop, db_getter: Any = None) -> None:
    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = str(context.get("message") or "Unhandled asyncio exception")
        error_id = make_error_id("ASYNC")
        ctx = sanitize_context({key: str(value) for key, value in context.items() if key != "exception"})
        if exc:
            error_log.exception("Unhandled asyncio exception error_id=%s message=%s context=%s", error_id, message, ctx, exc_info=exc)
        else:
            error_log.error("Unhandled asyncio exception error_id=%s message=%s context=%s", error_id, message, ctx)
        db = db_getter() if callable(db_getter) else None
        if db is not None:
            loop.create_task(record_exception_event(db, source="asyncio", error=exc or RuntimeError(message), error_id=error_id, context=ctx))

    loop.set_exception_handler(handler)


async def ensure_error_logging_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS error_events (
            error_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            error_type TEXT NOT NULL,
            message TEXT,
            traceback TEXT,
            context_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_error_events_created ON error_events(created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_error_events_source ON error_events(source, created_at DESC)")
    await conn.commit()


async def record_exception_event(db: Any, *, source: str, error: BaseException, error_id: str, context: dict[str, Any] | None = None) -> None:
    try:
        await ensure_error_logging_table(db)
        conn = db.require_conn()
        tb_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))[:MAX_TRACEBACK_CHARS]
        context_json = json.dumps(sanitize_context(context or {}))[:MAX_CONTEXT_CHARS]
        await conn.execute(
            """
            INSERT OR REPLACE INTO error_events (error_id, source, error_type, message, traceback, context_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (error_id, source, type(error).__name__, str(error)[:1200], tb_text, context_json, utc_now_iso()),
        )
        await conn.commit()
    except Exception as exc:
        error_log.warning("Failed to record error event error_id=%s source=%s: %s", error_id, source, exc)


async def fetch_recent_error_events(db: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    try:
        await ensure_error_logging_table(db)
        conn = db.require_conn()
        cursor = await conn.execute(
            """
            SELECT error_id, source, error_type, message, created_at
            FROM error_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 25)),),
        )
        return [dict(row) for row in await cursor.fetchall()]
    except Exception as exc:
        error_log.warning("Failed to fetch recent error events: %s", exc)
        return []


def install_discord_error_handlers(bot: commands.Bot) -> None:
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        underlying = getattr(error, "original", error)
        error_id = make_error_id("CMD")
        context = interaction_context(interaction)
        error_log.exception(
            "Slash command failed error_id=%s command=%s user=%s guild=%s channel=%s",
            error_id,
            context.get("command"),
            context.get("user_id"),
            context.get("guild_id"),
            context.get("channel_id"),
            exc_info=underlying,
        )
        await record_exception_event(getattr(bot, "db", None), source="app_command", error=underlying, error_id=error_id, context=context)
        await send_error_notice(interaction, error_id)

    bot.tree.on_error = on_app_command_error


async def log_command_error(bot: commands.Bot, interaction: discord.Interaction, error: BaseException, *, source: str = "command_error") -> str:
    underlying = getattr(error, "original", error)
    error_id = make_error_id("CMD")
    context = interaction_context(interaction)
    error_log.exception(
        "Command error handled locally error_id=%s source=%s command=%s user=%s guild=%s channel=%s",
        error_id,
        source,
        context.get("command"),
        context.get("user_id"),
        context.get("guild_id"),
        context.get("channel_id"),
        exc_info=underlying,
    )
    await record_exception_event(getattr(bot, "db", None), source=source, error=underlying, error_id=error_id, context=context)
    return error_id


async def send_error_notice(interaction: discord.Interaction, error_id: str, extra: str | None = None) -> None:
    message = (
        "SniperPlug hit an internal error, but I logged it with a trace ID so we can fix the real cause.\n"
        f"Error ID: `{error_id}`"
    )
    if extra:
        message += f"\n{extra}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        error_log.warning("Could not send Discord error notice error_id=%s", error_id)


def interaction_context(interaction: discord.Interaction) -> dict[str, Any]:
    command_name = None
    try:
        command_name = interaction.command.qualified_name if interaction.command else None
    except Exception:
        command_name = None
    return sanitize_context(
        {
            "command": command_name,
            "user_id": getattr(interaction.user, "id", None),
            "user": str(interaction.user) if interaction.user else None,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "response_done": interaction.response.is_done(),
        }
    )


def sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    blocked_tokens = ("token", "secret", "private", "key", "authorization", "auth", "signature", "password")
    cleaned: dict[str, Any] = {}
    for key, value in (context or {}).items():
        key_text = str(key)
        if any(token in key_text.lower() for token in blocked_tokens):
            cleaned[key_text] = "[redacted]"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key_text] = value
        else:
            cleaned[key_text] = str(value)[:500]
    return cleaned


def make_error_id(prefix: str = "ERR") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default
