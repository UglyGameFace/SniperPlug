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
        purpose="Configure whether verified deals may post publicly and where.",
        when_to_use="Use for public channel and public-posting retailer settings. This is not the same as auto-scan.",
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
        purpose="Configure which retailers may be scanned automatically in the background.",
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
        name="/sniperplug_commands or /sniperplug commands",
        audience="Owner",
        purpose="Show the command guide.",
        when_to_use="Use when anyone is confused about which SniperPlug command does what.",
    ),
    CommandCatalogEntry(
        name="/sniperplug setup",
        audience="Owner",
        purpose="Set fallback default alert channel.",
        when_to_use="Use during first-time setup or when changing the default deal channel.",
    ),
    CommandCatalogEntry(
        name="/sniperplug set_channel",
        audience="Owner",
        purpose="Route a specific alert type to a specific channel.",
        when_to_use="Use when different kinds of deals need different channels.",
    ),
    CommandCatalogEntry(
        name="/sniperplug status",
        audience="Owner",
        purpose="Show default channel and route channel status.",
        when_to_use="Use for old route/channel setup checks. Prefer `/sniperplug_dashboard` for full health.",
    ),
    CommandCatalogEntry(
        name="/sniperplug providers",
        audience="Owner",
        purpose="Show provider health only.",
        when_to_use="Use to check whether Walmart, Home Depot, Best Buy, or other providers are ready/staged/blocked.",
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
