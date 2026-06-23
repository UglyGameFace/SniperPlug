from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandCatalogEntry:
    name: str
    audience: str
    purpose: str
    when_to_use: str
    credit_risk: str = "none"


COMMAND_CATALOG: tuple[CommandCatalogEntry, ...] = (
    CommandCatalogEntry(
        name="/setup_sniperplug_here",
        audience="Owner",
        purpose="Fastest setup: use the current channel for public alerts, default route, retailers, and Walmart auto-scan.",
        when_to_use="Run this inside the channel where SniperPlug should post verified deals. This avoids Discord channel-picker/mobile channel-id issues.",
    ),
    CommandCatalogEntry(
        name="/setup_sniperplug",
        audience="Owner",
        purpose="Advanced one-step setup where you pick the alert channel manually.",
        when_to_use="Use this when setup from the current channel is not enough. If Discord says channel id invalid, use `/setup_sniperplug_here` instead.",
    ),
    CommandCatalogEntry(
        name="/sniperplug_workflow",
        audience="Everyone",
        purpose="Show the simple SniperPlug workflow from setup to posting.",
        when_to_use="Use when anyone is confused about what to run next.",
    ),
    CommandCatalogEntry(
        name="/deals",
        audience="Everyone",
        purpose="Simple Walmart deal search.",
        when_to_use="Use when you know the product words, like `turtle wax`, `gaming headset`, or `lego`.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/hunt",
        audience="Everyone",
        purpose="Button-based category hunt.",
        when_to_use="Use when you do not know what to search and want SniperPlug to pick preset categories.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/discover",
        audience="Everyone",
        purpose="Manual broad discovery run across deal categories.",
        when_to_use="Use when you want a newspaper-style scan now. This is manual and does not depend on auto-scan being enabled.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/autoscan_now",
        audience="Owner",
        purpose="Run the Walmart auto-scan immediately and show the exact post/block decision.",
        when_to_use="Use after deploys or setup changes to confirm whether deals post, duplicate, cache, fail confidence, or fail channel/config gates. Setup first with `/setup_sniperplug_here`.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/autoscan_health",
        audience="Owner",
        purpose="Diagnose Walmart auto-scan setup, channel, schedule gate, cache, and last-run decision.",
        when_to_use="Use when auto-scan posts zero deals or you need to know exactly which gate stopped it.",
    ),
    CommandCatalogEntry(
        name="/walmart_scan",
        audience="Staff",
        purpose="Advanced Walmart scan with page, sort, discount, and alert-only controls.",
        when_to_use="Use for deeper staff testing when `/deals` is too simple.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/home_depot_search",
        audience="Staff",
        purpose="Manual Home Depot SerpApi search with quota protection.",
        when_to_use="Use for Home Depot product candidates. Results are verification candidates, not confirmed in-store deals.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/home_depot_penny_hunt",
        audience="Staff",
        purpose="Home Depot penny/clearance candidate hunt through SerpApi.",
        when_to_use="Use with a ZIP or store ID when hunting local Home Depot penny-style leads.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/hd_stock",
        audience="Staff",
        purpose="Home Depot SKU + ZIP stock/price proof checker.",
        when_to_use="Use when you have a Home Depot SKU or Internet # and want a Hidden-Clearances-style local proof card before posting.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/hd_penny_zip",
        audience="Staff",
        purpose="ZIP-anchored Home Depot penny/clearance starter scan.",
        when_to_use="Use to quickly scan a ZIP for Home Depot clearance candidates. This is V1 ranking, not a locked ZIP penny database yet.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/local_check",
        audience="Staff",
        purpose="Private local inventory proof preview.",
        when_to_use="Use when you have SKU/UPC/store/ZIP info and need a safe proof card without public posting.",
    ),
    CommandCatalogEntry(
        name="/seed_clearance",
        audience="Staff",
        purpose="Save a manual clearance lead into the server bank.",
        when_to_use="Use when you find a lead manually and want SniperPlug to remember it for future checks.",
    ),
    CommandCatalogEntry(
        name="/clearance_bank",
        audience="Staff",
        purpose="List saved manual clearance leads.",
        when_to_use="Use to review manually seeded Home Depot/Walmart/local clearance leads.",
    ),
    CommandCatalogEntry(
        name="/active_deals",
        audience="Owner",
        purpose="Show cached active deals SniperPlug has recently seen.",
        when_to_use="Use when a scan cached deals but did not publicly post, or when checking what is still active.",
    ),
    CommandCatalogEntry(
        name="/active_deals_cleanup",
        audience="Owner",
        purpose="Mark old cached active deals stale.",
        when_to_use="Use when active cache has old deals that have not been seen again recently.",
    ),
    CommandCatalogEntry(
        name="/public_alerts",
        audience="Owner",
        purpose="Public posting settings: on/off, channel, and allowed retailer list.",
        when_to_use="Use only when `/setup_sniperplug_here` is not specific enough. This is not the same as auto-scan.",
    ),
    CommandCatalogEntry(
        name="/public_alerts_status",
        audience="Owner",
        purpose="Show current public posting settings.",
        when_to_use="Use to confirm whether public posting is enabled, which retailers may post, and which channel receives alerts.",
    ),
    CommandCatalogEntry(
        name="/retailer_autoscan",
        audience="Owner",
        purpose="Advanced scheduled/background scan settings.",
        when_to_use="Use to protect paid/free-tier API credits. Manual commands still work when auto-scan is off.",
    ),
    CommandCatalogEntry(
        name="/retailer_autoscan_status",
        audience="Owner",
        purpose="Show which retailers are allowed in background auto-scan.",
        when_to_use="Use to verify scheduled scan settings and credit gates.",
    ),
    CommandCatalogEntry(
        name="/sniperplug_dashboard",
        audience="Owner",
        purpose="One-page health/settings dashboard.",
        when_to_use="Use after deploys and when troubleshooting posting, scans, providers, or active cache.",
    ),
    CommandCatalogEntry(
        name="/sniperplug_health",
        audience="Owner",
        purpose="Show DB, cache, quota, provider, scan-run, and query-memory health.",
        when_to_use="Use after deploys or when cache/provider behavior looks wrong.",
    ),
    CommandCatalogEntry(
        name="/sniperplug_doctor",
        audience="Owner",
        purpose="Post-deploy self-check for DB, providers, caches, slash commands, safety checks, and recent errors.",
        when_to_use="Run this first after every deploy before testing deal commands.",
    ),
    CommandCatalogEntry(
        name="/sniperplug_commands",
        audience="Owner",
        purpose="Show the command guide.",
        when_to_use="Use when anyone is confused about which SniperPlug command does what.",
    ),
    CommandCatalogEntry(
        name="/deal_threshold",
        audience="Owner",
        purpose="Set the starting verified discount percent for deal hunting and auto-scan.",
        when_to_use="Use 30–40% for normal hunting, lower for more results, or higher for stricter glitch-style alerts.",
    ),
    CommandCatalogEntry(
        name="/sniperplug set_channel",
        audience="Owner",
        purpose="Route a specific alert type to a specific channel.",
        when_to_use="Use when different kinds of deals need different channels.",
    ),
)


COMMAND_AUDIENCE_ORDER = ("Everyone", "Staff", "Owner")


def entries_for_audience(audience: str | None = None) -> tuple[CommandCatalogEntry, ...]:
    if not audience:
        return COMMAND_CATALOG
    normalized = audience.strip().lower()
    return tuple(entry for entry in COMMAND_CATALOG if entry.audience.lower() == normalized)


def validate_command_catalog() -> list[str]:
    errors: list[str] = []
    names = [entry.name for entry in COMMAND_CATALOG]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"Duplicate command names: {', '.join(duplicates)}")
    for entry in COMMAND_CATALOG:
        if not entry.name.startswith("/"):
            errors.append(f"Command must start with slash: {entry.name}")
        if not entry.purpose:
            errors.append(f"Missing purpose: {entry.name}")
        if not entry.when_to_use:
            errors.append(f"Missing when_to_use: {entry.name}")
    return errors
