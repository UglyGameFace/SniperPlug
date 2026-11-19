"""One-time announcement for the consolidation PR 2 command tree.

On the first boot after PR 2 ships, the bot posts one short note in
each server's configured public deal channel — the commands got
simpler, everything lives under /menu now, nothing was removed — and
pins it. A row in ``sniperplug_data_migrations`` (the same one-time
marker table the public-alert config uses) makes sure it can never
repeat: once the marker exists, every later boot is a no-op.

Failure rules, deliberately conservative: a server with no configured
channel is skipped silently; a failed send skips that server; a failed
pin never fails the post or the boot; and the whole pass is wrapped by
the caller so it can never block startup.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.public_alert_config import get_public_alert_config

log = logging.getLogger("sniperplug")

ANNOUNCEMENT_KEY = "20261119_consolidation_pr2_announcement"

# The plan's three lines (consolidation plan §4.5), verbatim.
ANNOUNCEMENT_TEXT = (
    "SniperPlug's commands got simpler.\n"
    "Everything is under /menu now.\n"
    "/walmart find, /walmart hunt and /wishlist work exactly as before."
)


async def _ensure_marker_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sniperplug_data_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def announcement_already_posted(db: Any) -> bool:
    await _ensure_marker_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT 1 FROM sniperplug_data_migrations WHERE migration_key = ? LIMIT 1",
        (ANNOUNCEMENT_KEY,),
    )
    return await cursor.fetchone() is not None


async def _mark_announcement_posted(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        "INSERT INTO sniperplug_data_migrations (migration_key, applied_at) VALUES (?, ?) "
        "ON CONFLICT(migration_key) DO NOTHING",
        (ANNOUNCEMENT_KEY, utc_now_iso()),
    )
    await conn.commit()


async def maybe_post_consolidation_announcement(bot: Any) -> int:
    """Post the announcement once, ever. Returns how many servers got it.

    Never raises for a per-server problem: each server is attempted in
    isolation, and the one-time marker is written after the pass so a
    repeat boot — or a reconnect — posts nothing.
    """

    db = getattr(bot, "db", None)
    if db is None:
        return 0
    if await announcement_already_posted(db):
        return 0

    posted = 0
    for guild in list(getattr(bot, "guilds", ()) or ()):
        try:
            config = await get_public_alert_config(db, int(guild.id))
        except Exception:
            log.exception(
                "Consolidation announcement: could not read deal channel for guild %s; skipping",
                getattr(guild, "id", "?"),
            )
            continue
        channel_id = config.get("channel_id")
        if not channel_id:
            continue
        try:
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                channel = await guild.fetch_channel(int(channel_id))
        except Exception:
            log.exception(
                "Consolidation announcement: could not resolve channel %s in guild %s; skipping",
                channel_id,
                getattr(guild, "id", "?"),
            )
            continue
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            message = await channel.send(ANNOUNCEMENT_TEXT)
        except Exception:
            log.exception(
                "Consolidation announcement: send failed in guild %s; skipping",
                getattr(guild, "id", "?"),
            )
            continue
        posted += 1
        try:
            await message.pin()
        except Exception:
            # A failed pin must not fail the post: the note is in the
            # channel either way, staff can pin it by hand.
            log.exception(
                "Consolidation announcement: pin failed in guild %s; the post itself landed",
                getattr(guild, "id", "?"),
            )

    await _mark_announcement_posted(db)
    return posted
