from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.services.public_alert_config import (
    get_public_alert_config,
    set_public_alert_config,
    decode_channel_id,
)
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.routing import DEFAULT_ROUTE


log = logging.getLogger("sniperplug.setup_self_heal")

CONFIG_TABLES = (
    "guild_public_alert_settings",
    "guild_retailer_auto_scan_settings",
    "guild_retailer_auto_scan_runs",
    "guild_alert_channels",
    "guild_public_deal_posts",
    "guild_active_deal_cache",
    "alert_dedupe",
)
GHOST_CLEANUP_ATTEMPTS = 3
GHOST_CLEANUP_RETRY_DELAY_SECONDS = 0.25

REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}

# Ordered from strongest/most intentional to broader fallback. Discovery only
# adopts a channel when the best matching tier has exactly one sendable result.
AUTO_DISCOVERY_CHANNEL_NAMES = (
    ("walmart-deals", "walmart_deals", "walmartdeals"),
    ("sniperplug-deals", "sniperplug_deals", "sniperplug"),
    ("deal-alerts", "deal_alerts", "deals-alerts"),
    ("deals",),
)


@dataclass
class SetupRepairResult:
    guild_id: int
    changed: bool = False
    human_action_required: bool = False
    channel_id: int | None = None
    reason: str = ""
    notes: list[str] | None = None
    config: dict | None = None

    def discord_line(self) -> str:
        notes = self.notes or []
        suffix = ("\n" + "\n".join(f"• {note}" for note in notes[:5])) if notes else ""
        if self.human_action_required:
            return f"⚠️ **Needs attention:** {self.reason or 'saved setup could not be repaired automatically.'}{suffix}"
        if self.changed:
            channel = f"<#{self.channel_id}>" if self.channel_id else "saved channel"
            return f"✅ **Self-healed:** repaired SniperPlug posting setup for {channel}.{suffix}"
        return f"✅ **Healthy:** saved setup is valid and does not need rerun.{suffix}"


async def repair_all_public_alert_setups(db: Any, bot: discord.Client) -> dict[str, int]:
    cleanup = await cleanup_ghost_setup_rows_detailed(db, bot)
    repaired = 0
    ok = 0
    needs_action = 0

    for guild in list(getattr(bot, "guilds", []) or []):
        result = await repair_public_alert_setup(db, bot, int(guild.id))
        if result.changed:
            repaired += 1
        elif result.human_action_required:
            needs_action += 1
        else:
            ok += 1

    return {
        "ghost_rows_found": cleanup["found"],
        "ghost_rows_deleted": cleanup["deleted"],
        "ghost_rows_remaining": cleanup["remaining"],
        "repaired": repaired,
        "healthy": ok,
        "needs_action": needs_action,
    }


async def cleanup_ghost_setup_rows(db: Any, bot: discord.Client) -> int:
    """Backward-compatible verified ghost cleanup count."""

    cleanup = await cleanup_ghost_setup_rows_detailed(db, bot)
    return cleanup["deleted"]


async def cleanup_ghost_setup_rows_detailed(db: Any, bot: discord.Client) -> dict[str, int]:
    """Delete and verify setup rows for guilds the bot no longer serves.

    Turso/libSQL can briefly return a stale read immediately after a remote write.
    Large Discord snowflakes can also be rounded by a numeric wire decoder unless
    SQLite casts them to text before returning them. Discovery therefore reads
    IDs as text and only then converts them to Python integers.
    """

    live_guild_ids = {int(guild.id) for guild in list(getattr(bot, "guilds", []) or [])}
    if not live_guild_ids:
        return {"found": 0, "deleted": 0, "remaining": 0}

    conn = db.require_conn()
    ghost_ids = await _discover_ghost_ids(conn, live_guild_ids)
    if not ghost_ids:
        return {"found": 0, "deleted": 0, "remaining": 0}

    remaining = set(ghost_ids)
    failures: list[str] = []
    for attempt in range(1, GHOST_CLEANUP_ATTEMPTS + 1):
        failures.extend(await _delete_ghost_rows_once(conn, remaining))
        await conn.commit()
        remaining = await _remaining_ghost_ids(conn, remaining)
        if not remaining:
            break
        if attempt < GHOST_CLEANUP_ATTEMPTS:
            await asyncio.sleep(GHOST_CLEANUP_RETRY_DELAY_SECONDS * attempt)

    deleted = len(ghost_ids - remaining)
    if remaining:
        log.error(
            "Ghost guild cleanup remained incomplete after %s attempts remaining=%s failures=%s",
            GHOST_CLEANUP_ATTEMPTS,
            sorted(remaining),
            failures[-8:],
        )
    elif failures:
        log.warning(
            "Ghost guild cleanup completed after recoverable table errors found=%s deleted=%s failures=%s",
            len(ghost_ids),
            deleted,
            failures[-8:],
        )

    return {
        "found": len(ghost_ids),
        "deleted": deleted,
        "remaining": len(remaining),
    }


async def _discover_ghost_ids(conn: Any, live_guild_ids: set[int]) -> set[int]:
    ghost_ids: set[int] = set()
    for table in CONFIG_TABLES:
        try:
            cursor = await conn.execute(
                f"SELECT DISTINCT CAST(guild_id AS TEXT) AS guild_id FROM {table}"
            )
            rows = await cursor.fetchall()
        except Exception as exc:
            if not _missing_table_error(exc):
                log.warning("Ghost guild discovery skipped table=%s error=%s", table, exc)
            continue
        for row in rows:
            guild_id = _guild_id_from_row(row)
            if guild_id is not None and guild_id not in live_guild_ids:
                ghost_ids.add(guild_id)
    return ghost_ids


async def _delete_ghost_rows_once(conn: Any, ghost_ids: set[int]) -> list[str]:
    failures: list[str] = []
    # Delete cache/history/dependent rows first and the canonical setup row last.
    for guild_id in sorted(ghost_ids):
        for table in reversed(CONFIG_TABLES):
            try:
                await conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            except Exception as exc:
                if _missing_table_error(exc):
                    continue
                failures.append(f"{table}:{guild_id}:{type(exc).__name__}:{exc}")
    return failures


async def _remaining_ghost_ids(conn: Any, ghost_ids: set[int]) -> set[int]:
    remaining: set[int] = set()
    for guild_id in sorted(ghost_ids):
        for table in CONFIG_TABLES:
            try:
                cursor = await conn.execute(
                    f"SELECT 1 AS present FROM {table} WHERE guild_id = ? LIMIT 1",
                    (guild_id,),
                )
                row = await cursor.fetchone()
            except Exception as exc:
                if _missing_table_error(exc):
                    continue
                log.warning(
                    "Ghost guild verification failed table=%s guild=%s error=%s",
                    table,
                    guild_id,
                    exc,
                )
                remaining.add(guild_id)
                break
            if row:
                remaining.add(guild_id)
                break
    return remaining


def _missing_table_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no such table" in text or "does not exist" in text or "unknown table" in text


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


async def repair_public_alert_setup(
    db: Any,
    bot: discord.Client,
    guild_id: int,
    *,
    target_channel: discord.TextChannel | None = None,
) -> SetupRepairResult:
    notes: list[str] = []
    guild_id = int(guild_id)
    guild = bot.get_guild(guild_id)

    config = await get_public_alert_config(db, guild_id)

    if guild is None:
        return SetupRepairResult(
            guild_id=guild_id,
            human_action_required=True,
            reason=f"bot is not connected to guild `{guild_id}`.",
            notes=["Ghost/stale guild rows are cleaned on startup when possible."],
            config=config,
        )

    channel_candidates = await saved_channel_candidates(db, config, target_channel, guild_id=guild_id)
    channel, missing, source = first_sendable_channel(guild, channel_candidates)

    discovery_matches: list[discord.TextChannel] = []
    if channel is None:
        channel, discovery_matches = discover_unambiguous_deal_channel(guild)
        if channel is not None:
            source = "unambiguous server channel discovery"
            missing = []

    if channel is None:
        if len(discovery_matches) > 1:
            mentions = ", ".join(item.mention for item in discovery_matches[:8])
            return SetupRepairResult(
                guild_id=guild_id,
                human_action_required=True,
                reason="multiple possible deal channels were found, so SniperPlug refused to guess.",
                notes=[
                    f"Possible channels: {mentions}",
                    "Run `/autoscan_now force:true` once inside the exact channel you want; that safely saves it without redoing every setting.",
                ],
                config=config,
            )

        reason = "no saved or unambiguous sendable public deal channel was found."
        if missing:
            reason = f"saved channel exists, but bot is missing: {', '.join(missing)}."
        return SetupRepairResult(
            guild_id=guild_id,
            human_action_required=True,
            reason=reason,
            notes=[
                "This is a real first-install/permission issue, not an update issue.",
                "Run `/autoscan_now force:true` once inside the channel SniperPlug should post in, or fix the channel permissions.",
            ],
            config=config,
        )

    changed = False
    retailers = tuple(dict.fromkeys(
        normalize_retailer_key(value)
        for value in (*tuple(config.get("retailers") or ()), "walmart")
        if normalize_retailer_key(value)
    ))

    if (
        not config.get("enabled")
        or decode_channel_id(config.get("channel_id")) != int(channel.id)
        or "walmart" not in set(config.get("retailers") or ())
    ):
        await set_public_alert_config(
            db,
            guild_id=guild_id,
            enabled=True,
            retailers=retailers,
            channel_id=int(channel.id),
        )
        changed = True
        notes.append(f"public alerts saved to #{channel.name} from {source}")

    try:
        from sniperplug.cogs.public_alerts import list_retailer_auto_scan_settings, set_retailer_auto_scan

        settings = await list_retailer_auto_scan_settings(db, guild_id)
        walmart = settings.get("walmart", {})
        if not walmart.get("enabled") or int(walmart.get("interval_hours", 6)) != 0 or int(walmart.get("daily_limit", 25)) != 0:
            await set_retailer_auto_scan(db, guild_id, "walmart", True, interval_hours=0, daily_limit=0)
            changed = True
            notes.append("Walmart background auto-scan restored; runtime six-hour safety floor still applies")
    except Exception as exc:
        notes.append(f"auto-scan repair skipped safely: {exc}")

    fresh_config = await get_public_alert_config(db, guild_id)
    return SetupRepairResult(
        guild_id=guild_id,
        changed=changed,
        human_action_required=False,
        channel_id=int(channel.id),
        reason="repaired" if changed else "healthy",
        notes=notes,
        config=fresh_config,
    )


async def saved_channel_candidates(db: Any, config: dict, target_channel: discord.TextChannel | None, guild_id: int | None = None) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []

    public_channel = decode_channel_id(config.get("channel_id"))
    if public_channel:
        candidates.append((public_channel, "public alert config"))

    if guild_id is not None:
        try:
            deal_channel = await db.get_guild_deal_channel(int(guild_id))
        except Exception:
            deal_channel = None
        if deal_channel:
            candidates.append((int(deal_channel), "saved default deal route"))
        try:
            default_route = await db.get_alert_route(int(guild_id), DEFAULT_ROUTE)
        except Exception:
            default_route = None
        if default_route:
            candidates.append((int(default_route), "saved default alert route"))

    if target_channel is not None:
        safe_name = str(getattr(target_channel, "name", "") or "").lower()
        if any(token in safe_name for token in ("deal", "walmart", "clearance", "sniper")):
            candidates.append((int(target_channel.id), "current command channel"))

    seen: set[int] = set()
    unique: list[tuple[int, str]] = []
    for channel_id, source in candidates:
        if int(channel_id) in seen:
            continue
        seen.add(int(channel_id))
        unique.append((int(channel_id), source))
    return unique


def normalize_channel_name(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "-")


def discover_unambiguous_deal_channel(
    guild: discord.Guild,
) -> tuple[discord.TextChannel | None, list[discord.TextChannel]]:
    """Find one clearly intended, sendable deal channel without guessing.

    Matching is tiered. A single `walmart-deals` wins even if a generic `deals`
    channel also exists. Multiple sendable matches inside the strongest tier are
    returned as ambiguous so an owner can choose explicitly.
    """

    member = getattr(guild, "me", None)
    text_channels = [
        channel
        for channel in list(getattr(guild, "text_channels", []) or [])
        if isinstance(channel, discord.TextChannel)
    ]

    for aliases in AUTO_DISCOVERY_CHANNEL_NAMES:
        alias_set = {normalize_channel_name(alias) for alias in aliases}
        matches = [
            channel
            for channel in text_channels
            if normalize_channel_name(channel.name) in alias_set
            and not missing_channel_permissions(channel, member)
        ]
        if len(matches) == 1:
            return matches[0], matches
        if len(matches) > 1:
            return None, matches

    return None, []


def first_sendable_channel(guild: discord.Guild, candidates: list[tuple[int, str]]) -> tuple[discord.TextChannel | None, list[str], str]:
    last_missing: list[str] = []
    for channel_id, source in candidates:
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            continue
        missing = missing_channel_permissions(channel, getattr(guild, "me", None))
        if missing:
            last_missing = missing
            continue
        return channel, [], source
    return None, last_missing, ""


def missing_channel_permissions(channel: discord.TextChannel, member: discord.Member | None) -> list[str]:
    if member is None:
        return []
    perms = channel.permissions_for(member)
    missing: list[str] = []
    for attr, label in REQUIRED_CHANNEL_PERMS.items():
        if not getattr(perms, attr, False):
            missing.append(label)
    return missing
