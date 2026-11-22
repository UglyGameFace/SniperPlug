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


# Optional permissions for THIS announcement path only. Pinning was split
# out of Manage Messages into its own Pin Messages permission in Feb 2026,
# so a bot granted Manage Messages before the split can post but not pin.
# This is deliberately NOT part of setup_self_heal.REQUIRED_CHANNEL_PERMS:
# deal channels must keep working in servers that grant deal posting
# without pinning, and a failed pin here is already fail-safe.
OPTIONAL_ANNOUNCEMENT_PERMS = {
    "pin_messages": "Pin Messages",
}


def missing_optional_announcement_permissions(channel: Any, member: Any) -> list[str]:
    """Optional announcement permissions the bot lacks in ``channel``.

    Returns [] when the check cannot be performed (no member, no
    permissions_for) so the pin attempt proceeds exactly as before.
    """

    if member is None:
        return []
    permissions_for = getattr(channel, "permissions_for", None)
    if permissions_for is None:
        return []
    try:
        perms = permissions_for(member)
    except Exception:
        return []
    return [
        label
        for attr, label in OPTIONAL_ANNOUNCEMENT_PERMS.items()
        if not getattr(perms, attr, True)
    ]


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
        missing_optional = missing_optional_announcement_permissions(
            channel, getattr(guild, "me", None)
        )
        if missing_optional:
            log.warning(
                "Consolidation announcement: bot is missing %s in guild %s; the post landed, "
                "but pinning needs the Pin Messages permission (split from Manage Messages "
                "in Feb 2026). The pin will still be attempted; staff can pin it by hand if "
                "it fails.",
                ", ".join(missing_optional),
                getattr(guild, "id", "?"),
            )
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
