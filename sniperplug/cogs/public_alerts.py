from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.public_posting import (
    SUPPORTED_RETAILERS,
    format_retailers,
    normalize_retailer_key,
    parse_retailer_list,
    retailer_credit_note,
)

DEFAULT_AUTOSCAN_INTERVAL_HOURS = 6
DEFAULT_AUTOSCAN_DAILY_LIMIT = 25


class PublicAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="public_alerts", description="Configure whether SniperPlug may post verified deals publicly.")
    @app_commands.describe(
        enabled="Turn public posting on or off.",
        retailers="Comma list: walmart, home_depot, bestbuy, amazon. Leave blank to keep existing stores.",
        channel="Optional public channel to post deal alerts into.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def public_alerts(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        retailers: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which public alert settings to update.", ephemeral=True)
            return

        await ensure_public_alert_table(self.bot.db)
        existing = await get_public_alert_config(self.bot.db, interaction.guild_id)
        parsed_retailers = parse_retailer_list(retailers) if retailers is not None else existing["retailers"]
        if enabled and not parsed_retailers:
            await interaction.followup.send(
                "Public posting needs at least one enabled store. Example: `/public_alerts enabled:true retailers:walmart,home_depot channel:#price-glitch`",
                ephemeral=True,
            )
            return

        channel_id = channel.id if channel else existing["channel_id"]
        if enabled and not channel_id:
            await interaction.followup.send("Public posting needs a channel. Pick one with the `channel` option first.", ephemeral=True)
            return

        await set_public_alert_config(
            self.bot.db,
            guild_id=interaction.guild_id,
            enabled=enabled,
            retailers=parsed_retailers,
            channel_id=channel_id,
        )
        embed = public_alert_status_embed(enabled=enabled, retailers=parsed_retailers, channel_id=channel_id)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="public_alerts_status", description="Show SniperPlug public posting settings for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def public_alerts_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which settings to show.", ephemeral=True)
            return
        await ensure_public_alert_table(self.bot.db)
        config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        await ensure_retailer_auto_scan_table(self.bot.db)
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        await interaction.followup.send(
            embed=public_alert_status_embed(
                enabled=config["enabled"],
                retailers=config["retailers"],
                channel_id=config["channel_id"],
                auto_scan=auto_scan,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="retailer_autoscan", description="Toggle which stores SniperPlug may scan automatically to protect API credits.")
    @app_commands.describe(
        retailer="Store to toggle: walmart, home_depot, bestbuy, amazon.",
        enabled="Allow this store in automatic multi-store scans. Manual commands still work.",
        interval_hours="Minimum hours between automatic scans for this store. Default keeps current value or 6.",
        daily_limit="Max automatic scans per day for this store. Use 0 to block spending even if enabled.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def retailer_autoscan(
        self,
        interaction: discord.Interaction,
        retailer: str,
        enabled: bool,
        interval_hours: app_commands.Range[int, 1, 168] | None = None,
        daily_limit: app_commands.Range[int, 0, 500] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which retailer auto-scan setting to update.", ephemeral=True)
            return
        key = normalize_retailer_key(retailer)
        if key not in SUPPORTED_RETAILERS:
            await interaction.followup.send(
                f"Unknown retailer `{retailer}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}",
                ephemeral=True,
            )
            return
        await set_retailer_auto_scan(
            self.bot.db,
            interaction.guild_id,
            key,
            enabled,
            interval_hours=interval_hours,
            daily_limit=daily_limit,
        )
        settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = retailer_auto_scan_embed(settings)
        updated = settings[key]
        embed.add_field(
            name="Updated",
            value=(
                f"`{key}` auto-scan is now **{'on' if updated['enabled'] else 'off'}**.\n"
                f"Interval: **every {updated['interval_hours']} hour(s)**\n"
                f"Daily limit: **{updated['daily_limit']} automatic scan(s)**\n"
                f"{retailer_credit_note(key)}"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="retailer_autoscan_status", description="Show which stores are allowed in automatic multi-store scans.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def retailer_autoscan_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which settings to show.", ephemeral=True)
            return
        settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        await interaction.followup.send(embed=retailer_auto_scan_embed(settings), ephemeral=True)


def public_alert_status_embed(*, enabled: bool, retailers: tuple[str, ...], channel_id: int | None, auto_scan: dict[str, dict] | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="📣 Public Alert Settings",
        description="Public posting only applies to verified alertable deals. Weak proof and staff-review candidates stay private.",
        color=discord.Color.green() if enabled else discord.Color.dark_gold(),
    )
    embed.add_field(name="Enabled", value="Yes" if enabled else "No", inline=True)
    embed.add_field(name="Public stores", value=format_retailers(retailers), inline=True)
    embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "not set", inline=True)
    if auto_scan is not None:
        embed.add_field(name="Auto-scan stores", value=format_auto_scan_status(auto_scan), inline=False)
    embed.add_field(
        name="Credit safety",
        value="Public posting and auto-scanning are separate. A store can be allowed for public posting while still blocked from automatic scans that spend credits.",
        inline=False,
    )
    embed.set_footer(text="More stores can be added later without changing the command format.")
    return embed


def retailer_auto_scan_embed(settings: dict[str, dict]) -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Retailer Auto-Scan Settings",
        description="Controls which stores SniperPlug may include in automatic multi-store scans. Manual store-specific commands still work even when auto-scan is off.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Stores", value=format_auto_scan_status(settings), inline=False)
    embed.add_field(
        name="Why this exists",
        value="This protects free tiers and paid/limited APIs. Turn on only the stores you intentionally want SniperPlug to pull automatically, then set intervals and daily limits to cap credit usage.",
        inline=False,
    )
    return embed


def format_auto_scan_status(settings: dict[str, dict]) -> str:
    rows = []
    for retailer in sorted(SUPPORTED_RETAILERS):
        config = settings.get(retailer, default_auto_scan_config(retailer))
        enabled = bool(config.get("enabled"))
        interval_hours = int(config.get("interval_hours") or DEFAULT_AUTOSCAN_INTERVAL_HOURS)
        daily_limit = int(config.get("daily_limit") if config.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
        rows.append(f"{'✅' if enabled else '⛔'} `{retailer}` • every {interval_hours}h • max {daily_limit}/day")
    return "\n".join(rows)


def default_auto_scan_config(retailer: str) -> dict:
    return {
        "retailer": normalize_retailer_key(retailer),
        "enabled": False,
        "interval_hours": DEFAULT_AUTOSCAN_INTERVAL_HOURS,
        "daily_limit": DEFAULT_AUTOSCAN_DAILY_LIMIT,
    }


async def ensure_public_alert_table(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_alert_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            retailers_json TEXT NOT NULL DEFAULT '[]',
            channel_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def ensure_retailer_auto_scan_table(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
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
        """
    )
    await maybe_add_column(conn, "guild_retailer_auto_scan_settings", "interval_hours", "INTEGER NOT NULL DEFAULT 6")
    await maybe_add_column(conn, "guild_retailer_auto_scan_settings", "daily_limit", "INTEGER NOT NULL DEFAULT 25")
    await conn.commit()


async def maybe_add_column(conn, table: str, column: str, definition: str) -> None:
    try:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception as exc:
        if "duplicate column" not in str(exc).lower():
            raise


async def get_public_alert_config(db, guild_id: int) -> dict:
    import json
    from sniperplug.models.deal import utc_now_iso

    await ensure_public_alert_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT enabled, retailers_json, channel_id FROM guild_public_alert_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    if not row:
        now = utc_now_iso()
        await conn.execute(
            "INSERT INTO guild_public_alert_settings (guild_id, enabled, retailers_json, channel_id, created_at, updated_at) VALUES (?, 0, '[]', NULL, ?, ?)",
            (guild_id, now, now),
        )
        await conn.commit()
        return {"enabled": False, "retailers": (), "channel_id": None}
    try:
        retailers = tuple(normalize_retailer_key(value) for value in json.loads(row["retailers_json"] or "[]"))
    except Exception:
        retailers = ()
    return {"enabled": bool(row["enabled"]), "retailers": retailers, "channel_id": int(row["channel_id"]) if row["channel_id"] else None}


async def set_public_alert_config(db, *, guild_id: int, enabled: bool, retailers: tuple[str, ...], channel_id: int | None) -> None:
    import json
    from sniperplug.models.deal import utc_now_iso

    await ensure_public_alert_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    await conn.execute(
        """
        INSERT INTO guild_public_alert_settings (guild_id, enabled, retailers_json, channel_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled = excluded.enabled,
            retailers_json = excluded.retailers_json,
            channel_id = excluded.channel_id,
            updated_at = excluded.updated_at
        """,
        (guild_id, int(enabled), json.dumps(list(retailers)), channel_id, now, now),
    )
    await conn.commit()


async def set_retailer_auto_scan(
    db,
    guild_id: int,
    retailer: str,
    enabled: bool,
    *,
    interval_hours: int | None = None,
    daily_limit: int | None = None,
) -> None:
    from sniperplug.models.deal import utc_now_iso

    await ensure_retailer_auto_scan_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    key = normalize_retailer_key(retailer)
    existing = (await list_retailer_auto_scan_settings(db, guild_id)).get(key, default_auto_scan_config(key))
    next_interval = interval_hours if interval_hours is not None else int(existing.get("interval_hours") or DEFAULT_AUTOSCAN_INTERVAL_HOURS)
    next_daily_limit = daily_limit if daily_limit is not None else int(existing.get("daily_limit") if existing.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
    await conn.execute(
        """
        INSERT INTO guild_retailer_auto_scan_settings (guild_id, retailer, enabled, interval_hours, daily_limit, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, retailer) DO UPDATE SET
            enabled = excluded.enabled,
            interval_hours = excluded.interval_hours,
            daily_limit = excluded.daily_limit,
            updated_at = excluded.updated_at
        """,
        (guild_id, key, int(enabled), next_interval, next_daily_limit, now, now),
    )
    await conn.commit()


async def list_retailer_auto_scan_settings(db, guild_id: int) -> dict[str, dict]:
    await ensure_retailer_auto_scan_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT retailer, enabled, interval_hours, daily_limit FROM guild_retailer_auto_scan_settings WHERE guild_id = ?",
        (guild_id,),
    )
    rows = await cursor.fetchall()
    settings = {retailer: default_auto_scan_config(retailer) for retailer in SUPPORTED_RETAILERS}
    for row in rows:
        key = normalize_retailer_key(row["retailer"])
        if key in SUPPORTED_RETAILERS:
            settings[key] = {
                "retailer": key,
                "enabled": bool(row["enabled"]),
                "interval_hours": int(row["interval_hours"] or DEFAULT_AUTOSCAN_INTERVAL_HOURS),
                "daily_limit": int(row["daily_limit"] if row["daily_limit"] is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT),
            }
    return settings
