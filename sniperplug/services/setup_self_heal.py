from __future__ import annotations

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


CONFIG_TABLES = (
    "guild_public_alert_settings",
    "guild_retailer_auto_scan_settings",
    "guild_retailer_auto_scan_runs",
    "guild_alert_channels",
    "guild_public_deal_posts",
    "guild_active_deal_cache",
    "alert_dedupe",
)

REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}


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
    deleted = await cleanup_ghost_setup_rows(db, bot)
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
        "ghost_rows_deleted": deleted,
        "repaired": repaired,
        "healthy": ok,
        "needs_action": needs_action,
    }


async def cleanup_ghost_setup_rows(db: Any, bot: discord.Client) -> int:
    live_guild_ids = {int(guild.id) for guild in list(getattr(bot, "guilds", []) or [])}
    if not live_guild_ids:
        return 0

    conn = db.require_conn()
    ghost_ids: set[int] = set()

    for table in CONFIG_TABLES:
        try:
            cursor = await conn.execute(f"SELECT DISTINCT guild_id FROM {table}")
            rows = await cursor.fetchall()
        except Exception:
            continue
        for row in rows:
            try:
                guild_id = int(row["guild_id"])
            except Exception:
                continue
            if guild_id not in live_guild_ids:
                ghost_ids.add(guild_id)

    for guild_id in ghost_ids:
        for table in CONFIG_TABLES:
            try:
                await conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            except Exception:
                pass

    if ghost_ids:
        await conn.commit()
    return len(ghost_ids)


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

    if channel is None:
        reason = "no saved sendable public deal channel was found."
        if missing:
            reason = f"saved channel exists, but bot is missing: {', '.join(missing)}."
        return SetupRepairResult(
            guild_id=guild_id,
            human_action_required=True,
            reason=reason,
            notes=[
                "This is a real first-install/permission issue, not an update issue.",
                "Run setup only once inside the channel SniperPlug should post in, or fix the channel permissions.",
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
            notes.append("Walmart background auto-scan restored with official-provider unlimited gates")
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
