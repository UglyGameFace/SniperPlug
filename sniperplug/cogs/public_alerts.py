from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.public_posting import format_retailers, normalize_retailer_key, parse_retailer_list


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
        await interaction.followup.send(
            embed=public_alert_status_embed(
                enabled=config["enabled"],
                retailers=config["retailers"],
                channel_id=config["channel_id"],
            ),
            ephemeral=True,
        )


def public_alert_status_embed(*, enabled: bool, retailers: tuple[str, ...], channel_id: int | None) -> discord.Embed:
    embed = discord.Embed(
        title="📣 Public Alert Settings",
        description="Public posting only applies to verified alertable deals. Weak proof and staff-review candidates stay private.",
        color=discord.Color.green() if enabled else discord.Color.dark_gold(),
    )
    embed.add_field(name="Enabled", value="Yes" if enabled else "No", inline=True)
    embed.add_field(name="Stores", value=format_retailers(retailers), inline=True)
    embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "not set", inline=True)
    embed.set_footer(text="More stores can be added later without changing the command format.")
    return embed


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
