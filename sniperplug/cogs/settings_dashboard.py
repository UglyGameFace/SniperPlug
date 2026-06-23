from __future__ import annotations

import os
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.active_deals import active_deal_counts
from sniperplug.cogs.public_alerts import format_auto_scan_status, get_public_alert_config, list_retailer_auto_scan_settings
from sniperplug.providers import serpapi_home_depot as home_depot_search_cache_module
from sniperplug.providers.registry import provider_registry
from sniperplug.services import embed_delivery as embed_delivery_module
from sniperplug.services import home_depot_product_lookup as home_depot_detail_cache_module
from sniperplug.services.command_catalog import COMMAND_AUDIENCE_ORDER, CommandCatalogEntry, entries_for_audience
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent, set_starting_deal_percent
from sniperplug.services.error_logging import fetch_recent_error_events
from sniperplug.services.public_posting import format_retailers
from sniperplug.services.quota_guard import serpapi_quota_guard


class SettingsDashboardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="sniperplug_dashboard", description="Show SniperPlug posting, auto-scan, provider, and cache status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sniperplug_dashboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I can show that server settings.", ephemeral=True)
            return

        public_config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        provider_health = await provider_registry.healthchecks()
        active_counts = await active_deal_counts(self.bot.db, interaction.guild_id)
        starting_percent = await get_starting_deal_percent(self.bot.db, interaction.guild_id)
        channel_id = public_config.get("channel_id")
        channel_text = str(channel_id) if channel_id else "not set"

        embed = discord.Embed(title="SniperPlug Dashboard", description="Settings that decide whether SniperPlug scans, caches, and posts deals.", color=discord.Color.blue())
        embed.add_field(name="Deal finder threshold", value=f"Starting verified markdown: **{starting_percent}%+**\nChange with `/deal_threshold percent:30`.", inline=False)
        embed.add_field(name="Public posting", value=f"Enabled: {'yes' if public_config['enabled'] else 'no'}\nChannel ID: {channel_text}\nRetailers: {format_retailers(public_config['retailers'])}", inline=False)
        embed.add_field(name="Auto-scan retailers", value=format_auto_scan_status(auto_scan), inline=False)
        embed.add_field(name="Active cache", value=format_active_counts(active_counts), inline=False)
        embed.add_field(name="Provider health", value=format_provider_health(provider_health), inline=False)
        embed.add_field(name="Recommended owner checks", value="Run `/sniperplug_doctor`, `/sniperplug_health`, `/sniperplug_commands`, `/public_alerts_status`, `/retailer_autoscan_status`, and `/active_deals` after each deploy.", inline=False)
        embed.set_footer(text="Manual commands can run even when auto-scan is off. Auto-scan only controls scheduled pulls.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="sniperplug_health", description="Show SniperPlug DB, cache, quota, and recent scan health.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sniperplug_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I can show server-specific scan health.", ephemeral=True)
            return

        db = self.bot.db
        try:
            await db.prune_expired_cache()
        except Exception:
            pass

        provider_health = await provider_registry.healthchecks()
        health = await build_db_health_snapshot(db, interaction.guild_id)
        quota = serpapi_quota_guard.check(interaction.user.id, cost=0)

        backend = getattr(db, "backend", "unknown")
        connected = "yes" if getattr(db, "conn", None) is not None else "no"
        embed = discord.Embed(
            title="SniperPlug Health",
            description="Live backend/cache view so we can stop guessing from screenshots.",
            color=discord.Color.green() if connected == "yes" else discord.Color.red(),
        )
        embed.add_field(name="Database", value=f"Backend: **{backend}**\nConnected: **{connected}**", inline=True)
        embed.add_field(
            name="SerpApi quota guard",
            value=(
                f"Daily: **{quota.daily_used}/{quota.daily_limit}**\n"
                f"Hourly/user: **{quota.hourly_user_used}/{quota.hourly_user_limit}**\n"
                f"Monthly safe: **{quota.monthly_used}/{quota.monthly_limit}**"
            ),
            inline=True,
        )
        embed.add_field(name="Providers", value=format_provider_health(provider_health), inline=False)
        embed.add_field(name="Cache tables", value=format_cache_counts(health.get("counts", {})), inline=False)
        embed.add_field(name="Recent scan runs", value=format_recent_scans(health.get("recent_scans", [])), inline=False)
        embed.add_field(name="Top query memory", value=format_query_memory(health.get("top_queries", [])), inline=False)
        embed.set_footer(text="Tip: run the same scan twice. Cache hits should rise and provider calls should not.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @sniperplug_health.error
    async def sniperplug_health_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to view SniperPlug health." if isinstance(error, app_commands.MissingPermissions) else f"SniperPlug health hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="sniperplug_doctor", description="Run a post-deploy SniperPlug self-check for DB, cache, providers, and safety checks.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sniperplug_doctor(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I can run server-specific checks.", ephemeral=True)
            return

        db = self.bot.db
        provider_health = await provider_registry.healthchecks()
        health = await build_db_health_snapshot(db, interaction.guild_id)
        errors = await fetch_recent_error_events(db, limit=5)
        checks = await build_doctor_checks(self.bot, interaction.guild_id, provider_health, health)
        failed = [check for check in checks if check[0] == "FAIL"]
        warnings = [check for check in checks if check[0] == "WARN"]
        color = discord.Color.red() if failed else discord.Color.orange() if warnings else discord.Color.green()
        status = "FAIL" if failed else "WARN" if warnings else "PASS"

        embed = discord.Embed(
            title=f"SniperPlug Doctor • {status}",
            description="Post-deploy smoke check for the stuff that keeps biting us: DB, cache, providers, slash commands, safety checks, and recent errors.",
            color=color,
        )
        embed.add_field(name="Core checks", value=format_doctor_checks(checks), inline=False)
        embed.add_field(name="Providers", value=format_provider_health(provider_health), inline=False)
        embed.add_field(name="DB/cache counts", value=format_cache_counts(health.get("counts", {})), inline=False)
        embed.add_field(name="Recent errors", value=format_recent_errors(errors), inline=False)
        embed.add_field(name="Next action", value=doctor_next_action(failed, warnings), inline=False)
        embed.set_footer(text="Run this after every deploy before testing deal commands.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @sniperplug_doctor.error
    async def sniperplug_doctor_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to run SniperPlug Doctor." if isinstance(error, app_commands.MissingPermissions) else f"SniperPlug Doctor hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="deal_threshold", description="Set the starting verified discount percent for /deals and /hunt.")
    @app_commands.describe(percent="Starting verified markdown percent. Lower shows more results. Try 20, 30, 40, or 50.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deal_threshold(self, interaction: discord.Interaction, percent: app_commands.Range[int, 0, 95]) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I can save the server threshold.", ephemeral=True)
            return
        saved = await set_starting_deal_percent(self.bot.db, interaction.guild_id, int(percent))
        embed = discord.Embed(
            title="Deal threshold updated",
            description=(
                f"SniperPlug will now start `/deals` and `/hunt` at **{saved}%+ verified markdown**.\n\n"
                "Lower numbers show more results. Higher numbers are stricter and may hide profitable flip/value leads."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Recommended", value="Use **30–40%** for normal deal hunting. Use **50%+** only when you want stricter glitch-style markdowns.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @deal_threshold.error
    async def deal_threshold_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to change the deal threshold." if isinstance(error, app_commands.MissingPermissions) else f"Deal threshold hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="sniperplug_commands", description="Show what each SniperPlug command is for so nobody has to guess.")
    @app_commands.describe(audience="Optional filter: everyone, staff, or owner.")
    @app_commands.choices(
        audience=[
            app_commands.Choice(name="Everyone", value="everyone"),
            app_commands.Choice(name="Staff", value="staff"),
            app_commands.Choice(name="Owner", value="owner"),
        ]
    )
    async def sniperplug_commands(self, interaction: discord.Interaction, audience: app_commands.Choice[str] | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        entries = entries_for_audience(audience.value if audience else None)
        embed = build_command_guide_embed(entries, audience.value if audience else None)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def build_db_health_snapshot(db: Any, guild_id: int) -> dict[str, Any]:
    conn = db.require_conn()
    counts = {}
    for table in (
        "provider_response_cache",
        "scan_result_cache",
        "product_identity",
        "price_observations",
        "alert_dedupe",
        "store_cache",
        "scan_runs",
        "query_performance_memory",
        "error_events",
        "deals",
    ):
        counts[table] = await table_count(conn, table)

    recent_scans = await fetch_recent_scan_summary(conn, guild_id)
    top_queries = await fetch_top_query_memory(conn, guild_id)
    return {"counts": counts, "recent_scans": recent_scans, "top_queries": top_queries}


async def build_doctor_checks(bot: commands.Bot, guild_id: int, provider_health: list[Any], health: dict[str, Any]) -> list[tuple[str, str, str]]:
    db = getattr(bot, "db", None)
    counts = health.get("counts", {})
    checks: list[tuple[str, str, str]] = []
    checks.append(check("Database connection", db is not None and getattr(db, "conn", None) is not None, f"backend={getattr(db, 'backend', 'unknown')}"))
    checks.append(check("error_events table", counts.get("error_events", -1) >= 0, f"rows={counts.get('error_events', 'n/a')}"))
    checks.append(check("Native embed delivery", hasattr(embed_delivery_module, "sanitize_embed") and hasattr(embed_delivery_module, "batch_embeds_for_limit"), "safe embed sizing lives in native send helpers; no boot monkey patch required"))
    checks.append(check("Home Depot search cache", getattr(home_depot_search_cache_module, "_CACHE_DB", None) is not None, "shared DB cache installed"))
    checks.append(check("Home Depot detail cache", getattr(home_depot_detail_cache_module, "_CACHE_DB", None) is not None, "shared DB cache installed"))
    required_providers = {"walmart", "home_depot", "home_depot_serpapi"}
    registered = set(provider_registry.list_keys())
    checks.append(check("Required providers", required_providers.issubset(registered), f"registered={', '.join(sorted(registered)) or 'none'}"))
    ready_count = sum(1 for item in provider_health if getattr(item, "ok", False))
    checks.append(check("Provider healthchecks", ready_count > 0, f"ready={ready_count}/{len(provider_health)}"))
    command_count = len(bot.tree.get_commands())
    checks.append(check("Slash commands loaded", command_count >= 20, f"loaded={command_count}"))
    checks.append(check_env("SERPAPI_API_KEY", required=False, label="SerpApi key"))
    checks.append(check_env("WALMART_CONSUMER_ID", required=False, label="Walmart consumer ID"))
    checks.append(check_env("WALMART_PRIVATE_KEY_B64", required=False, label="Walmart private key"))
    checks.append(check("Message content intent", True, f"enabled={getattr(bot.intents, 'message_content', False)}; slash commands do not require it"))
    return checks


async def table_count(conn: Any, table: str) -> int:
    if table not in {
        "provider_response_cache",
        "scan_result_cache",
        "product_identity",
        "price_observations",
        "alert_dedupe",
        "store_cache",
        "scan_runs",
        "query_performance_memory",
        "error_events",
        "deals",
    }:
        return 0
    cursor = await conn.execute(f"SELECT COUNT(*) AS count FROM {table}")
    row = await cursor.fetchone()
    return int(row["count"] if row else 0)


async def fetch_recent_scan_summary(conn: Any, guild_id: int) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        SELECT retailer,
               COUNT(*) AS scans,
               SUM(provider_calls) AS provider_calls,
               SUM(cache_hits) AS cache_hits,
               SUM(cache_misses) AS cache_misses,
               SUM(results_found) AS results_found
        FROM scan_runs
        WHERE guild_id = ? OR guild_id IS NULL
        GROUP BY retailer
        ORDER BY scans DESC
        LIMIT 6
        """,
        (guild_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def fetch_top_query_memory(conn: Any, guild_id: int) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        SELECT retailer, query, scans, returned_products, verified_hits, review_hits, blocked_hits, score
        FROM query_performance_memory
        WHERE guild_id = ?
        ORDER BY score DESC
        LIMIT 5
        """,
        (guild_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def format_cache_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "No DB count data returned."
    labels = {
        "provider_response_cache": "provider cache",
        "scan_result_cache": "scan cache",
        "product_identity": "product identity",
        "price_observations": "price history",
        "alert_dedupe": "alert dedupe",
        "store_cache": "store cache",
        "scan_runs": "scan runs",
        "query_performance_memory": "query memory",
        "error_events": "error events",
        "deals": "deals table",
    }
    lines = [f"**{labels.get(key, key)}:** {value}" for key, value in counts.items()]
    return truncate("\n".join(lines), 1024)


def format_recent_scans(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No scan runs recorded yet. Run `/deals` or `/walmart_scan` twice after redeploy."
    lines = []
    for row in rows[:6]:
        retailer = row.get("retailer") or "unknown"
        lines.append(
            f"**{retailer}:** scans {row.get('scans') or 0} • provider {row.get('provider_calls') or 0} • cache hit {row.get('cache_hits') or 0} • miss {row.get('cache_misses') or 0} • results {row.get('results_found') or 0}"
        )
    return truncate("\n".join(lines), 1024)


def format_query_memory(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No query memory yet. It fills as `/hunt`, `/deals`, and `/walmart_scan` run."
    lines = []
    for row in rows[:5]:
        lines.append(
            f"**{trim(row.get('query') or 'unknown', 35)}** ({row.get('retailer')}) — score {float(row.get('score') or 0):.1f}, scans {row.get('scans') or 0}, hits {row.get('verified_hits') or 0}/{row.get('review_hits') or 0}, blocked {row.get('blocked_hits') or 0}"
        )
    return truncate("\n".join(lines), 1024)


def format_doctor_checks(checks: list[tuple[str, str, str]]) -> str:
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
    lines = [f"{icon.get(status, '•')} **{name}:** {detail}" for status, name, detail in checks]
    return truncate("\n".join(lines), 1024)


def format_recent_errors(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return "No stored errors yet. Beautifully boring."
    lines = []
    for error in errors[:5]:
        lines.append(f"`{error.get('error_id')}` **{error.get('source')}** {error.get('error_type')}: {trim(error.get('message') or '', 90)}")
    return truncate("\n".join(lines), 1024)


def doctor_next_action(failed: list[tuple[str, str, str]], warnings: list[tuple[str, str, str]]) -> str:
    if failed:
        return "Fix the ❌ checks before testing `/deals` or auto-scan. Start with the first failed line."
    if warnings:
        return "Safe to test manually, but review the ⚠️ checks before turning on auto-scan/public posting."
    return "Looks clean. Test `/deals search:lego` twice, then rerun `/sniperplug_health` to confirm scan/run data updates."


def check(name: str, passed: bool, detail: str, *, warn: bool = False) -> tuple[str, str, str]:
    status = "PASS" if passed else "WARN" if warn else "FAIL"
    return status, name, detail


def check_env(name: str, *, required: bool, label: str) -> tuple[str, str, str]:
    present = bool(os.getenv(name, "").strip())
    status = "PASS" if present else "FAIL" if required else "WARN"
    detail = "set" if present else "missing; related features may be disabled"
    return status, label, detail


def build_command_guide_embed(entries: tuple[CommandCatalogEntry, ...], audience: str | None = None) -> discord.Embed:
    title = "SniperPlug Command Guide"
    description = "Simple names, clear purpose. Manual scans are different from scheduled auto-scan. Public posting is different from auto-scan."
    if audience:
        description += f"\nFiltered to: **{audience.title()}**"
    embed = discord.Embed(title=title, description=description, color=discord.Color.orange())

    grouped: dict[str, list[CommandCatalogEntry]] = {name: [] for name in COMMAND_AUDIENCE_ORDER}
    for entry in entries:
        grouped.setdefault(entry.audience, []).append(entry)

    for group_name in COMMAND_AUDIENCE_ORDER:
        group_entries = grouped.get(group_name) or []
        if not group_entries:
            continue
        lines: list[str] = []
        for entry in group_entries:
            credit = f" Credit/API: {entry.credit_risk}." if entry.credit_risk and entry.credit_risk != "none" else ""
            lines.append(f"**{entry.name}** — {entry.purpose}\nUse when: {entry.when_to_use}{credit}")
        embed.add_field(name=group_name, value=truncate("\n\n".join(lines), 1024), inline=False)

    embed.set_footer(text="Owner tip: use /sniperplug_dashboard when something feels wrong.")
    return embed


def format_active_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "No active deals cached yet."
    return "\n".join(f"{retailer}: {count}" for retailer, count in sorted(counts.items()))


def format_provider_health(healthchecks) -> str:
    if not healthchecks:
        return "No providers registered."
    rows = []
    for health in healthchecks:
        status = getattr(health.status, "value", str(health.status))
        icon = "ready" if health.ok else "staged" if status == "staged" else "blocked"
        rows.append(f"{icon}: {health.provider_key} - {status} - {trim(health.message, 120)}")
    return truncate("\n".join(rows[:10]), 1024)


def trim(value: Any, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def truncate(value: str, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
