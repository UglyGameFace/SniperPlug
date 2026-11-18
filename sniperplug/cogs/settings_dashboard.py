from __future__ import annotations

import os
from typing import Any

import discord
from discord.ext import commands

from sniperplug.providers import serpapi_home_depot as home_depot_search_cache_module
from sniperplug.providers.registry import provider_registry
from sniperplug.services import embed_delivery as embed_delivery_module
from sniperplug.services import home_depot_product_lookup as home_depot_detail_cache_module
from sniperplug.services.command_catalog import CommandCatalogEntry


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
        return "No scan runs recorded yet. Run `/walmart find` twice after redeploy."
    lines = []
    for row in rows[:6]:
        retailer = row.get("retailer") or "unknown"
        lines.append(
            f"**{retailer}:** scans {row.get('scans') or 0} • provider {row.get('provider_calls') or 0} • cache hit {row.get('cache_hits') or 0} • miss {row.get('cache_misses') or 0} • results {row.get('results_found') or 0}"
        )
    return truncate("\n".join(lines), 1024)


def format_query_memory(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No query memory yet. It fills as `/walmart hunt` and `/walmart find` run."
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
        return "Fix the ❌ checks before testing `/walmart find` or auto-scan. Start with the first failed line."
    if warnings:
        return "Safe to test manually, but review the ⚠️ checks before turning on auto-scan/public posting."
    return "Looks clean. Test `/walmart find search:lego` twice, then rerun `/sniperplug_dashboard` to confirm scan/run data updates."


def check(name: str, passed: bool, detail: str, *, warn: bool = False) -> tuple[str, str, str]:
    status = "PASS" if passed else "WARN" if warn else "FAIL"
    return status, name, detail


def check_env(name: str, *, required: bool, label: str) -> tuple[str, str, str]:
    present = bool(os.getenv(name, "").strip())
    status = "PASS" if present else "FAIL" if required else "WARN"
    detail = "set" if present else "missing; related features may be disabled"
    return status, label, detail


PRIMARY_COMMANDS = {"/walmart find", "/walmart hunt", "/walmart discover"}
SPECIALIST_COMMANDS = {
    "/home_depot_search",
    "/home_depot_penny_hunt",
    "/hd_stock",
    "/local_check",
    "/seed_clearance",
    "/clearance_bank",
}
ADVANCED_COMMANDS = {
    "/walmart find",
    "/sniperplug_dashboard",
}


def command_guide_section(entry: CommandCatalogEntry) -> str:
    if entry.name in PRIMARY_COMMANDS:
        return "Start here"
    if entry.name in SPECIALIST_COMMANDS:
        return "Special searches"
    if entry.name in ADVANCED_COMMANDS:
        return "Advanced / diagnostic"
    if entry.audience == "Owner":
        return "Owner setup and health"
    return "Helpful shortcuts"


def build_command_guide_embed(entries: tuple[CommandCatalogEntry, ...], audience: str | None = None) -> discord.Embed:
    title = "SniperPlug Command Guide"
    description = (
        "Start with **`/walmart find`**, **`/walmart hunt`**, or **`/walmart discover`**. "
        "Specialist and diagnostic commands are grouped separately so you do not have to understand the whole bot first."
    )
    if audience:
        description += f"\nFiltered to: **{audience.title()}**"
    embed = discord.Embed(title=title, description=description, color=discord.Color.orange())

    section_order = ("Start here", "Special searches", "Owner setup and health", "Helpful shortcuts", "Advanced / diagnostic")
    grouped: dict[str, list[CommandCatalogEntry]] = {name: [] for name in section_order}
    for entry in entries:
        grouped[command_guide_section(entry)].append(entry)

    for section in section_order:
        section_entries = grouped.get(section) or []
        if not section_entries:
            continue
        lines: list[str] = []
        for entry in section_entries:
            credit = f" Credit/API: {entry.credit_risk}." if entry.credit_risk and entry.credit_risk != "none" else ""
            lines.append(f"**{entry.name}** — {entry.purpose}\nUse when: {entry.when_to_use}{credit}")
        embed.add_field(name=section, value=truncate("\n\n".join(lines), 1024), inline=False)

    embed.set_footer(text="Not sure? Run /sniperplug_workflow. Owners: use /sniperplug_dashboard when something feels wrong.")
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
