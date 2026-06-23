from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.autoscan_history import format_latest_report_line, latest_autoscan_report
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent, set_starting_deal_percent
from sniperplug.services.public_alert_config import get_public_alert_config, set_public_alert_config
from sniperplug.services.public_posting import (
    SUPPORTED_RETAILERS,
    format_retailers,
    normalize_retailer_key,
)


DEFAULT_AUTOSCAN_INTERVAL_HOURS = 6
DEFAULT_AUTOSCAN_DAILY_LIMIT = 25
UNLIMITED_AUTOSCAN_INTERVAL_HOURS = 0
UNLIMITED_AUTOSCAN_DAILY_LIMIT = 0
UNMETERED_OFFICIAL_RETAILERS = {"walmart"}
WALMART_AUTOSCAN_SCAN_KEY = "autoscan:walmart_discovery"


class PublicAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="autoscan_setup", description="One-command setup for Walmart public auto-scan alerts.")
    @app_commands.describe(
        channel="Public channel where verified Walmart deals should post.",
        threshold="Minimum verified markdown percent before auto-scan/public alerts consider a deal.",
        unlimited="For Walmart official scans, bypass interval/daily gates. Paid-credit stores stay protected elsewhere.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        threshold: app_commands.Range[int, 0, 95] = 40,
        unlimited: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to update.", ephemeral=True)
            return

        safe_threshold = await set_starting_deal_percent(self.bot.db, interaction.guild_id, int(threshold))
        await set_public_alert_config(
            self.bot.db,
            guild_id=interaction.guild_id,
            enabled=True,
            retailers=("walmart",),
            channel_id=channel.id,
        )
        interval_hours = UNLIMITED_AUTOSCAN_INTERVAL_HOURS if unlimited else DEFAULT_AUTOSCAN_INTERVAL_HOURS
        daily_limit = UNLIMITED_AUTOSCAN_DAILY_LIMIT if unlimited else DEFAULT_AUTOSCAN_DAILY_LIMIT
        await set_retailer_auto_scan(
            self.bot.db,
            interaction.guild_id,
            "walmart",
            True,
            interval_hours=interval_hours,
            daily_limit=daily_limit,
        )
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = autoscan_setup_complete_embed(channel_id=channel.id, threshold=safe_threshold, auto_scan=auto_scan)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @autoscan_setup.error
    async def autoscan_setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to set up auto-scan alerts." if isinstance(error, app_commands.MissingPermissions) else f"Auto-scan setup hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="retailer_autoscan", description="Turn scheduled auto-scan on/off for a retailer and set its safety gates.")
    @app_commands.describe(
        retailer="Retailer key, like walmart. Supported stores are shown in /retailer_autoscan_status.",
        enabled="Whether scheduled/background auto-scan may run for this retailer.",
        interval_hours="Hours between scheduled runs. Use 0 only for official/unmetered providers like Walmart.",
        daily_limit="Max scheduled runs per day. Use 0 only for official/unmetered providers like Walmart.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def retailer_autoscan(
        self,
        interaction: discord.Interaction,
        retailer: str,
        enabled: bool,
        interval_hours: app_commands.Range[int, 0, 168] = DEFAULT_AUTOSCAN_INTERVAL_HOURS,
        daily_limit: app_commands.Range[int, 0, 250] = DEFAULT_AUTOSCAN_DAILY_LIMIT,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to save.", ephemeral=True)
            return
        key = normalize_retailer_key(retailer)
        if key not in SUPPORTED_RETAILERS:
            await interaction.followup.send(f"Unsupported retailer `{retailer}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}", ephemeral=True)
            return

        safe_interval = int(interval_hours)
        safe_daily = int(daily_limit)
        if key not in UNMETERED_OFFICIAL_RETAILERS:
            if safe_interval <= 0:
                safe_interval = DEFAULT_AUTOSCAN_INTERVAL_HOURS
            if safe_daily <= 0:
                safe_daily = DEFAULT_AUTOSCAN_DAILY_LIMIT

        await set_retailer_auto_scan(
            self.bot.db,
            interaction.guild_id,
            key,
            bool(enabled),
            interval_hours=safe_interval,
            daily_limit=safe_daily,
        )
        settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = discord.Embed(
            title="Retailer auto-scan updated",
            description=(
                f"`{key}` scheduled auto-scan is now **{'on' if enabled else 'off'}**.\n"
                "Manual `/deals`, `/hunt`, and `/discover` are still allowed even when background auto-scan is off."
            ),
            color=discord.Color.green() if enabled else discord.Color.orange(),
        )
        embed.add_field(name="Current auto-scan settings", value=format_auto_scan_status(settings), inline=False)
        if key in UNMETERED_OFFICIAL_RETAILERS and safe_interval <= 0 and safe_daily <= 0:
            embed.add_field(name="Credit safety", value="Official/unmetered Walmart auto-scan bypass is enabled: no interval gate and no daily gate.", inline=False)
        elif int(interval_hours) <= 0 or int(daily_limit) <= 0:
            embed.add_field(name="Credit safety adjusted", value="This retailer is not marked official/unmetered, so zero gates were restored to safe defaults.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @retailer_autoscan.error
    async def retailer_autoscan_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to change retailer auto-scan." if isinstance(error, app_commands.MissingPermissions) else f"Retailer auto-scan hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="retailer_autoscan_status", description="Show scheduled auto-scan gates for each supported retailer.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def retailer_autoscan_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to show.", ephemeral=True)
            return
        settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = discord.Embed(
            title="Retailer Auto-Scan Status",
            description="Scheduled/background scan gates. Manual commands do not depend on these being enabled.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Retailers", value=format_auto_scan_status(settings), inline=False)
        embed.add_field(name="Tip", value="Use `/retailer_autoscan retailer:walmart enabled:true interval_hours:0 daily_limit:0` for unlimited official Walmart background scans.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @retailer_autoscan_status.error
    async def retailer_autoscan_status_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to view retailer auto-scan." if isinstance(error, app_commands.MissingPermissions) else f"Retailer auto-scan status hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="public_alerts_status", description="Show SniperPlug public posting and auto-scan settings for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def public_alerts_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which settings to show.", ephemeral=True)
            return
        config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        threshold = await get_starting_deal_percent(self.bot.db, interaction.guild_id)
        await interaction.followup.send(
            embed=public_alert_status_embed(
                enabled=config["enabled"],
                retailers=config["retailers"],
                channel_id=config["channel_id"],
                auto_scan=auto_scan,
                threshold=threshold,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="autoscan_health", description="Check whether Walmart auto-scan can post and what happened recently.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to check.", ephemeral=True)
            return
        await interaction.followup.send(embed=await build_autoscan_health_embed(self.bot, interaction.guild_id), ephemeral=True)

    @autoscan_health.error
    async def autoscan_health_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to check auto-scan health." if isinstance(error, app_commands.MissingPermissions) else f"Auto-scan health check hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def autoscan_setup_complete_embed(*, channel_id: int | str, threshold: int, auto_scan: dict[str, dict]) -> discord.Embed:
    embed = discord.Embed(
        title="✅ Walmart Auto-Scan Setup Complete",
        description="SniperPlug is now configured to scan Walmart automatically and post only verified public-ready deals.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Public posting", value=f"Enabled for `walmart` → <#{channel_id}>", inline=False)
    embed.add_field(name="Deal threshold", value=f"**{threshold}%+ verified markdown**. This applies to `/deals`, `/hunt`, `/discover`, and auto-scan.", inline=False)
    embed.add_field(name="Auto-scan", value=format_auto_scan_status(auto_scan), inline=False)
    embed.add_field(
        name="What gets posted?",
        value=(
            "Auto-scan uses **Best Picks** ranking, then posts only cards that pass public-alert proof, duplicate checks, confidence, and fresh/new/lower-price checks. "
            "Weak proof and staff-review candidates stay private."
        ),
        inline=False,
    )
    embed.add_field(name="Optional test", value="Use `/autoscan_now` only when you want an immediate debug run. Use `/autoscan_health` to see whether setup/channel/runs look healthy.", inline=False)
    return embed


def public_alert_status_embed(*, enabled: bool, retailers: tuple[str, ...], channel_id: int | str | None, auto_scan: dict[str, dict] | None = None, threshold: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="📣 Public Alert Settings",
        description="This is the simple view. Use `/autoscan_setup` to change setup, `/deal_threshold` to adjust markdown, and `/autoscan_health` to diagnose posting.",
        color=discord.Color.green() if enabled else discord.Color.dark_gold(),
    )
    embed.add_field(name="Enabled", value="Yes" if enabled else "No", inline=True)
    embed.add_field(name="Public stores", value=format_retailers(retailers), inline=True)
    embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "not set", inline=True)
    if threshold is not None:
        embed.add_field(name="Deal threshold", value=f"{threshold}%+ verified markdown", inline=True)
    if auto_scan is not None:
        embed.add_field(name="Auto-scan stores", value=format_auto_scan_status(auto_scan), inline=False)
    embed.add_field(name="Posting logic", value="Auto-scan uses Best Picks ranking, then the public guard blocks same-price duplicates, weak proof, non-alertable cards, and low-confidence cards. Lower-price repeats can post again.", inline=False)
    embed.set_footer(text="Advanced public-alert controls are available through /retailer_autoscan and /retailer_autoscan_status.")
    return embed


async def build_autoscan_health_embed(bot: commands.Bot, guild_id: int) -> discord.Embed:
    db = bot.db
    config = await get_public_alert_config(db, guild_id)
    auto_scan = await list_retailer_auto_scan_settings(db, guild_id)
    threshold = await get_starting_deal_percent(db, guild_id)
    allowed, reason, walmart_settings = await auto_scan_allowed(db, guild_id, "walmart", scan_key=WALMART_AUTOSCAN_SCAN_KEY)
    last_run = await latest_auto_scan_run(db, guild_id, "walmart", scan_key=WALMART_AUTOSCAN_SCAN_KEY)
    latest_report = await latest_autoscan_report(db, guild_id=guild_id, retailer="walmart", scan_key=WALMART_AUTOSCAN_SCAN_KEY)
    posts_today = await count_public_posts_today(db, guild_id)
    active_cached = await count_active_cached_deals(db, guild_id)
    channel_status = public_alert_channel_status(bot, guild_id, config.get("channel_id"))

    critical_ok = bool(config.get("enabled")) and "walmart" in set(config.get("retailers") or ()) and channel_status.startswith("✅") and bool(walmart_settings.get("enabled")) and allowed
    embed = discord.Embed(
        title="🩺 Walmart Auto-Scan Health",
        description="This checks setup, channel permissions, schedule gates, and the exact last run decision trail.",
        color=discord.Color.green() if critical_ok else discord.Color.orange(),
    )
    embed.add_field(
        name="Setup",
        value=(
            f"Public alerts: **{'on' if config.get('enabled') else 'off'}**\n"
            f"Public stores: {format_retailers(tuple(config.get('retailers') or ())) }\n"
            f"Threshold: **{threshold}%+ verified markdown**\n"
            f"Walmart auto-scan: **{'on' if walmart_settings.get('enabled') else 'off'}**"
        ),
        inline=False,
    )
    embed.add_field(name="Channel", value=channel_status, inline=False)
    embed.add_field(
        name="Schedule gate",
        value=(
            f"Allowed now: **{'yes' if allowed else 'no'}**\n"
            f"Reason: {reason}\n"
            f"Interval: **{format_interval(int(walmart_settings.get('interval_hours', DEFAULT_AUTOSCAN_INTERVAL_HOURS)))}**\n"
            f"Daily limit: **{format_daily_limit(int(walmart_settings.get('daily_limit', DEFAULT_AUTOSCAN_DAILY_LIMIT)))}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Recent memory",
        value=(
            f"Last scheduled run: **{last_run or 'not logged yet'}**\n"
            f"Public posts today: **{posts_today}**\n"
            f"Active cached deals: **{active_cached}**"
        ),
        inline=False,
    )
    embed.add_field(name="Last run decision", value=trim_field(format_latest_report_line(latest_report), 1024), inline=False)
    embed.add_field(
        name="How to read this",
        value="If setup/channel/gate are green but posts stay at 0, check Last run decision. It will show whether threshold, confidence, fresh filter, duplicate, not-alertable, or disabled guards blocked the candidates.",
        inline=False,
    )
    return embed


def public_alert_channel_status(bot: commands.Bot, guild_id: int, channel_id: int | str | None) -> str:
    if not channel_id:
        return "⛔ No public channel saved. Run `/autoscan_setup channel:#walmart-deals`."
    guild = bot.get_guild(guild_id)
    if guild is None:
        return f"⛔ Bot is not connected to guild `{guild_id}` right now."
    decoded = decode_channel_id(channel_id)
    if decoded is None:
        return f"⛔ Saved channel ID is invalid: `{channel_id}`. Re-run `/autoscan_setup`."
    channel = guild.get_channel(decoded)
    if channel is None:
        return f"⛔ Saved channel <#{decoded}> is not visible in this guild cache. Re-run `/autoscan_setup` with the live channel."
    if not hasattr(channel, "send"):
        return f"⛔ Saved channel <#{decoded}> is not a sendable text channel."
    me = getattr(guild, "me", None)
    if me is not None and hasattr(channel, "permissions_for"):
        perms = channel.permissions_for(me)
        missing = []
        if not getattr(perms, "view_channel", True):
            missing.append("View Channel")
        if not getattr(perms, "send_messages", True):
            missing.append("Send Messages")
        if not getattr(perms, "embed_links", True):
            missing.append("Embed Links")
        if missing:
            return f"⛔ <#{decoded}> is saved, but bot is missing: {', '.join(missing)}."
    return f"✅ <#{decoded}> is saved and sendable."


def decode_channel_id(value: int | str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace("<#", "").replace(">", "")
    if text.startswith("ch:"):
        text = text[3:]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


async def latest_auto_scan_run(db, guild_id: int, retailer: str, *, scan_key: str) -> str | None:
    try:
        await ensure_retailer_auto_scan_run_table(db)
        conn = db.require_conn()
        key = normalize_retailer_key(retailer)
        cursor = await conn.execute(
            "SELECT ran_at FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND scan_key = ? ORDER BY ran_at DESC LIMIT 1",
            (guild_id, key, scan_key),
        )
        row = await cursor.fetchone()
        return str(row["ran_at"]) if row and row["ran_at"] else None
    except Exception:
        return None


async def count_public_posts_today(db, guild_id: int) -> int:
    try:
        conn = db.require_conn()
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM guild_public_deal_posts WHERE guild_id = ? AND status = 'posted' AND posted_at IS NOT NULL AND posted_at >= ?",
            (guild_id, since),
        )
        row = await cursor.fetchone()
        return int(row["count"] if row and row["count"] is not None else 0)
    except Exception:
        return 0


async def count_active_cached_deals(db, guild_id: int) -> int:
    try:
        conn = db.require_conn()
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_active_deal_cache WHERE guild_id = ? AND status = 'active'", (guild_id,))
        row = await cursor.fetchone()
        return int(row["count"] if row and row["count"] is not None else 0)
    except Exception:
        return 0


def trim_field(value: str, limit: int = 1024) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_auto_scan_status(settings: dict[str, dict]) -> str:
    rows = []
    for retailer in sorted(SUPPORTED_RETAILERS):
        config = settings.get(retailer, default_auto_scan_config(retailer))
        enabled = bool(config.get("enabled"))
        interval_hours = int(config.get("interval_hours") if config.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
        daily_limit = int(config.get("daily_limit") if config.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
        rows.append(f"{'✅' if enabled else '⛔'} `{retailer}` • {format_interval(interval_hours)} • {format_daily_limit(daily_limit)}")
    return "\n".join(rows)


def format_interval(interval_hours: int) -> str:
    return "no interval gate" if int(interval_hours) <= 0 else f"every {int(interval_hours)}h"


def format_daily_limit(daily_limit: int) -> str:
    return "no daily gate" if int(daily_limit) <= 0 else f"max {int(daily_limit)}/day"


def default_auto_scan_config(retailer: str) -> dict:
    key = normalize_retailer_key(retailer)
    return {"retailer": key, "enabled": False, "interval_hours": DEFAULT_AUTOSCAN_INTERVAL_HOURS, "daily_limit": DEFAULT_AUTOSCAN_DAILY_LIMIT}


async def ensure_retailer_auto_scan_table(db) -> None:
    conn = db.require_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_retailer_auto_scan_settings (
            guild_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            interval_hours INTEGER NOT NULL DEFAULT 6,
            daily_limit INTEGER NOT NULL DEFAULT 25,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, retailer)
        )
    """)
    await maybe_add_column(conn, "guild_retailer_auto_scan_settings", "interval_hours", "INTEGER NOT NULL DEFAULT 6")
    await maybe_add_column(conn, "guild_retailer_auto_scan_settings", "daily_limit", "INTEGER NOT NULL DEFAULT 25")
    await conn.commit()


async def ensure_retailer_auto_scan_run_table(db) -> None:
    conn = db.require_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_retailer_auto_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            scan_key TEXT NOT NULL,
            ran_at TEXT NOT NULL,
            day_key TEXT NOT NULL
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_scan_runs_guild_retailer_day ON guild_retailer_auto_scan_runs (guild_id, retailer, day_key)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_scan_runs_guild_retailer_key ON guild_retailer_auto_scan_runs (guild_id, retailer, scan_key, ran_at)")
    await conn.commit()


async def maybe_add_column(conn, table: str, column: str, definition: str) -> None:
    try:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception as exc:
        if "duplicate column" not in str(exc).lower():
            raise


async def set_retailer_auto_scan(db, guild_id: int, retailer: str, enabled: bool, *, interval_hours: int | None = None, daily_limit: int | None = None) -> None:
    await ensure_retailer_auto_scan_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    key = normalize_retailer_key(retailer)
    existing = (await list_retailer_auto_scan_settings(db, guild_id)).get(key, default_auto_scan_config(key))
    next_interval = interval_hours if interval_hours is not None else int(existing.get("interval_hours") if existing.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
    next_daily_limit = daily_limit if daily_limit is not None else int(existing.get("daily_limit") if existing.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
    await conn.execute("""
        INSERT INTO guild_retailer_auto_scan_settings (guild_id, retailer, enabled, interval_hours, daily_limit, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, retailer) DO UPDATE SET
            enabled = excluded.enabled,
            interval_hours = excluded.interval_hours,
            daily_limit = excluded.daily_limit,
            updated_at = excluded.updated_at
    """, (guild_id, key, int(enabled), next_interval, next_daily_limit, now, now))
    await conn.commit()


async def list_retailer_auto_scan_settings(db, guild_id: int) -> dict[str, dict]:
    await ensure_retailer_auto_scan_table(db)
    conn = db.require_conn()
    cursor = await conn.execute("SELECT retailer, enabled, interval_hours, daily_limit FROM guild_retailer_auto_scan_settings WHERE guild_id = ?", (guild_id,))
    rows = await cursor.fetchall()
    settings = {retailer: default_auto_scan_config(retailer) for retailer in SUPPORTED_RETAILERS}
    for row in rows:
        key = normalize_retailer_key(row["retailer"])
        if key in SUPPORTED_RETAILERS:
            settings[key] = {"retailer": key, "enabled": bool(row["enabled"]), "interval_hours": int(row["interval_hours"] if row["interval_hours"] is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS), "daily_limit": int(row["daily_limit"] if row["daily_limit"] is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)}
    return settings


async def auto_scan_allowed(db, guild_id: int, retailer: str, *, scan_key: str) -> tuple[bool, str, dict]:
    key = normalize_retailer_key(retailer)
    settings = (await list_retailer_auto_scan_settings(db, guild_id)).get(key, default_auto_scan_config(key))
    if not settings.get("enabled"):
        return False, f"`{key}` auto-scan is off", settings
    daily_limit = int(settings.get("daily_limit") if settings.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
    interval_hours = int(settings.get("interval_hours") if settings.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
    bypass_gates = key in UNMETERED_OFFICIAL_RETAILERS and daily_limit <= 0 and interval_hours <= 0
    if not bypass_gates and daily_limit <= 0:
        return False, f"`{key}` daily auto-scan limit is 0", settings
    await ensure_retailer_auto_scan_run_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    day_key = now.date().isoformat()
    cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND day_key = ?", (guild_id, key, day_key))
    row = await cursor.fetchone()
    used_today = int(row["count"] if row and row["count"] is not None else 0)
    if not bypass_gates and used_today >= daily_limit:
        return False, f"`{key}` daily auto-scan limit reached ({used_today}/{daily_limit})", settings
    if not bypass_gates and interval_hours > 0:
        cursor = await conn.execute("SELECT ran_at FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND scan_key = ? ORDER BY ran_at DESC LIMIT 1", (guild_id, key, scan_key))
        last = await cursor.fetchone()
        if last and last["ran_at"]:
            last_dt = datetime.fromisoformat(str(last["ran_at"]))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            next_allowed = last_dt + timedelta(hours=interval_hours)
            if now < next_allowed:
                minutes = max(1, int((next_allowed - now).total_seconds() // 60))
                return False, f"`{key}` interval gate: try again in about {minutes} minute(s)", settings
    if bypass_gates:
        return True, f"`{key}` auto-scan allowed with official-provider bypass ({used_today} runs logged today)", settings
    return True, f"`{key}` auto-scan allowed ({used_today}/{daily_limit} used today)", settings


async def record_auto_scan_run(db, guild_id: int, retailer: str, *, scan_key: str) -> None:
    await ensure_retailer_auto_scan_run_table(db)
    conn = db.require_conn()
    key = normalize_retailer_key(retailer)
    now = datetime.now(timezone.utc)
    await conn.execute("INSERT INTO guild_retailer_auto_scan_runs (guild_id, retailer, scan_key, ran_at, day_key) VALUES (?, ?, ?, ?, ?)", (guild_id, key, scan_key, now.isoformat(), now.date().isoformat()))
    await conn.commit()
