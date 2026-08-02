from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sniperplug.services.ghost_guild_tombstones import (
    clear_live_ghost_tombstones,
    load_ghost_tombstones,
    mark_ghost_tombstones,
)
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.setup_self_heal import (
    GHOST_CLEANUP_ATTEMPTS,
    GHOST_CLEANUP_RETRY_DELAY_SECONDS,
    _delete_ghost_rows_once,
    _discover_ghost_ids,
    _remaining_ghost_ids,
    repair_public_alert_setup,
)


log = logging.getLogger("sniperplug.autoscan.live_guilds")
_SESSION_GHOST_TOMBSTONES: set[int] = set()


@dataclass(frozen=True)
class LiveAutoScanGuild:
    guild_id: int
    channel_id: int | None


@dataclass(frozen=True)
class LiveGuildLoadResult:
    guilds: tuple[LiveAutoScanGuild, ...] = ()
    stale_visible_ids: tuple[int, ...] = ()
    tombstoned_visible_ids: tuple[int, ...] = ()


async def reconcile_live_public_alert_setups(db: Any, bot: Any) -> dict[str, int]:
    """Repair live guilds and quarantine non-live setup rows once.

    A Turso/libSQL replica can briefly surface a row after the connection that
    deleted it already verified the row absent. Durable tombstones are backed by
    a process-local quarantine set so a stale remote read cannot repeatedly
    announce and delete the same ghost every scheduler cycle.
    """

    live_guild_ids = {
        int(guild.id) for guild in list(getattr(bot, "guilds", []) or [])
    }
    if not live_guild_ids:
        return {
            "ghost_rows_found": 0,
            "ghost_rows_deleted": 0,
            "ghost_rows_remaining": 0,
            "ghost_rows_quarantined": 0,
            "ghost_rows_already_quarantined": 0,
            "repaired": 0,
            "healthy": 0,
            "needs_action": 0,
        }

    conn = db.require_conn()
    # Tombstone helpers commit their own schema and mutations. A guild that was
    # genuinely rejoined is released durably and from the session quarantine.
    await clear_live_ghost_tombstones(conn, live_guild_ids)
    _SESSION_GHOST_TOMBSTONES.difference_update(live_guild_ids)
    tombstoned = await load_ghost_tombstones(conn)
    tombstoned.update(_SESSION_GHOST_TOMBSTONES)
    visible_ghost_ids = await _discover_ghost_ids(conn, live_guild_ids)
    already_quarantined = visible_ghost_ids & tombstoned
    new_ghost_ids = visible_ghost_ids - tombstoned

    remaining = set(new_ghost_ids)
    failures: list[str] = []
    if new_ghost_ids:
        for attempt in range(1, GHOST_CLEANUP_ATTEMPTS + 1):
            failures.extend(await _delete_ghost_rows_once(conn, remaining))
            await conn.commit()
            remaining = await _remaining_ghost_ids(conn, remaining)
            if not remaining:
                break
            if attempt < GHOST_CLEANUP_ATTEMPTS:
                await asyncio.sleep(GHOST_CLEANUP_RETRY_DELAY_SECONDS * attempt)

        await mark_ghost_tombstones(
            conn,
            new_ghost_ids,
            reason="not_in_live_discord_guild_cache",
        )
        _SESSION_GHOST_TOMBSTONES.update(new_ghost_ids)

    deleted = len(new_ghost_ids - remaining)
    if remaining:
        log.warning(
            "Ghost guild rows quarantined after delete verification remained stale-visible "
            "guild_ids=%s failures=%s",
            sorted(remaining),
            failures[-8:],
        )
    elif new_ghost_ids:
        log.info(
            "Ghost guild setup cleanup verified and tombstoned guild_ids=%s",
            sorted(new_ghost_ids),
        )

    repaired = 0
    healthy = 0
    needs_action = 0
    for guild in list(getattr(bot, "guilds", []) or []):
        result = await repair_public_alert_setup(db, bot, int(guild.id))
        if result.changed:
            repaired += 1
        elif result.human_action_required:
            needs_action += 1
        else:
            healthy += 1

    return {
        "ghost_rows_found": len(new_ghost_ids),
        "ghost_rows_deleted": deleted,
        "ghost_rows_remaining": len(remaining),
        "ghost_rows_quarantined": len(new_ghost_ids),
        "ghost_rows_already_quarantined": len(already_quarantined),
        "repaired": repaired,
        "healthy": healthy,
        "needs_action": needs_action,
    }


async def list_live_public_alert_guilds(db: Any, bot: Any) -> LiveGuildLoadResult:
    """Load only guilds present in Discord's current live guild cache.

    This function never deletes. Reconciliation owns cleanup, while discovery
    only filters. That prevents two independent cleanup paths from racing or
    producing duplicate "deleted" warnings from inconsistent remote reads.
    """

    live_guild_ids = {
        int(guild.id) for guild in list(getattr(bot, "guilds", []) or [])
    }
    if not live_guild_ids:
        return LiveGuildLoadResult()

    conn = db.require_conn()
    tombstoned = await load_ghost_tombstones(conn)
    tombstoned.update(_SESSION_GHOST_TOMBSTONES)
    cursor = await conn.execute(
        "SELECT guild_id FROM guild_public_alert_settings "
        "WHERE enabled = 1 AND channel_id IS NOT NULL"
    )
    rows = await cursor.fetchall()

    guilds: list[LiveAutoScanGuild] = []
    stale_visible: set[int] = set()
    tombstoned_visible: set[int] = set()
    seen: set[int] = set()
    for row in rows:
        guild_id = _guild_id_from_row(row)
        if guild_id is None:
            log.warning("Skipped malformed public-alert row without a usable guild id: %r", row)
            continue
        if guild_id not in live_guild_ids:
            stale_visible.add(guild_id)
            if guild_id in tombstoned:
                tombstoned_visible.add(guild_id)
            continue
        if guild_id in seen:
            continue

        try:
            config = await get_public_alert_config(db, guild_id)
        except Exception:
            log.exception(
                "Skipped live guild because public-alert config could not be read guild=%s",
                guild_id,
            )
            continue
        if "walmart" not in set(config.get("retailers") or ()):
            continue
        try:
            channel_id = int(config.get("channel_id"))
        except (TypeError, ValueError):
            log.warning(
                "Skipped live guild with malformed public-alert channel id guild=%s channel=%r",
                guild_id,
                config.get("channel_id"),
            )
            continue
        seen.add(guild_id)
        guilds.append(LiveAutoScanGuild(guild_id=guild_id, channel_id=channel_id))

    return LiveGuildLoadResult(
        guilds=tuple(guilds),
        stale_visible_ids=tuple(sorted(stale_visible)),
        tombstoned_visible_ids=tuple(sorted(tombstoned_visible)),
    )


def is_live_bot_guild(bot: Any, guild_id: int) -> bool:
    try:
        return bot.get_guild(int(guild_id)) is not None
    except Exception:
        return False


def _guild_id_from_row(row: Any) -> int | None:
    values: list[Any] = []
    try:
        values.append(row["guild_id"])
    except Exception:
        pass
    try:
        values.append(row[0])
    except Exception:
        pass
    values.append(getattr(row, "guild_id", None))
    for value in values:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
