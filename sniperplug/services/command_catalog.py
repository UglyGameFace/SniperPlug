from __future__ import annotations

from dataclasses import dataclass

from sniperplug.services.command_surface import RETIRED_COMMAND_NAMES


@dataclass(frozen=True)
class CommandCatalogEntry:
    name: str
    audience: str
    purpose: str
    when_to_use: str
    credit_risk: str = "none"


# Canonical entry points only. Retired aliases, destructive maintenance commands,
# and obsolete per-server Walmart interval controls do not belong in user help.
COMMAND_CATALOG: tuple[CommandCatalogEntry, ...] = (
    CommandCatalogEntry(
        name="/walmart find",
        audience="Everyone",
        purpose="Search for a specific Walmart product using exact-detail verification.",
        when_to_use="Use when you know what you want, such as `gaming headset`, `lego`, or `detergent`.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/walmart check",
        audience="Everyone",
        purpose="Check one Walmart link or item ID directly, even when search never surfaces it.",
        when_to_use="Use when you have a Walmart link or item ID and want the exact-verified result.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/walmart hunt",
        audience="Everyone",
        purpose="Open category buttons for verified Walmart deal hunting.",
        when_to_use="Use when you want SniperPlug to pick category and value lanes for you, including open-box/restored coverage.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/walmart cash",
        audience="Everyone",
        purpose="Find only products with strict API-proven Walmart Cash offers.",
        when_to_use="Use for a manual Cash-only search. Global autoscan can attach the exact Cash amount to normal deal alerts automatically.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/ebay …",
        audience="Everyone",
        purpose="Explore live eBay listing asking prices and manage optional eBay message consent.",
        when_to_use="Use /ebay research for non-affiliate, on-demand listing comparisons; /ebay consent opts in or out of future promotion and /ebay review_sample previews disclosures. No eBay automatic sender exists until authorized.",
        credit_risk="eBay official Browse API when configured",
    ),
    CommandCatalogEntry(
        name="/walmart my-alerts",
        audience="Everyone",
        purpose="Enable, filter, test, pause, or delete personal exact-deal DM alerts.",
        when_to_use="Use when you want your own Smart, All, or Custom deal stream without changing a server's public feed.",
    ),
    CommandCatalogEntry(
        name="/walmart discover",
        audience="Everyone",
        purpose="Start an optional immediate Quick, Deep, or Full exact Walmart sweep.",
        when_to_use="Use for an on-demand sweep. Normal automatic coverage does not require this command because the global cursor scans continuously.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/home_depot_search",
        audience="Staff",
        purpose="Run a targeted Home Depot SerpApi product search with quota protection.",
        when_to_use="Use for private Home Depot shopper leads. Results are not automatically treated as confirmed local clearance.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/home_depot_penny_hunt",
        audience="Staff",
        purpose="Run a targeted ZIP/store-anchored Home Depot penny or clearance hunt.",
        when_to_use="Use instead of the retired ZIP-only alias so the query and location stay explicit.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/hd_stock",
        audience="Staff",
        purpose="Check one exact Home Depot SKU against a selected nearby store.",
        when_to_use="Use when you already have a SKU/Internet number and need store-specific private proof.",
        credit_risk="SerpApi credit",
    ),
    CommandCatalogEntry(
        name="/local_check",
        audience="Staff",
        purpose="Create a private local-inventory proof preview for a supported retailer.",
        when_to_use="Use when you have SKU, UPC, store, ZIP, or observed-price evidence and do not want public posting.",
    ),
    CommandCatalogEntry(
        name="/seed_clearance",
        audience="Staff",
        purpose="Save a manually found clearance lead to the server's review bank.",
        when_to_use="Use for leads found outside SniperPlug that staff want to track safely.",
    ),
    CommandCatalogEntry(
        name="/clearance_bank",
        audience="Staff",
        purpose="Review manually saved clearance leads.",
        when_to_use="Use to revisit staff-seeded local or retailer-specific leads.",
    ),
    CommandCatalogEntry(
        name="/walmart setup",
        audience="Owner",
        purpose="Choose this channel for public exact-deal delivery and apply the normal server defaults.",
        when_to_use="Run once during installation or when intentionally moving the posting channel. Global Walmart discovery itself is shared and continuous.",
    ),
    CommandCatalogEntry(
        name="/sniperplug_dashboard",
        audience="Owner",
        purpose="Open Overview, Doctor, or Commands from one owner dashboard.",
        when_to_use="Use instead of the retired workflow, health, doctor, commands, and raw status aliases.",
    ),
    CommandCatalogEntry(
        name="/walmart health",
        audience="Owner",
        purpose="Show global catalog progress, exact queue health, and this server's fanout enrollment.",
        when_to_use="Use when automatic deals are missing or after changing the posting channel.",
    ),
    CommandCatalogEntry(
        name="/walmart recovery",
        audience="Owner",
        purpose="Review recent exact Walmart events that did not post and choose a safe recovery action.",
        when_to_use="Retry current rules, recheck exact proof, or let the actual server owner post one soft-blocked event without weakening automatic settings.",
    ),
    CommandCatalogEntry(
        name="/walmart scan-now",
        audience="Owner",
        purpose="Run a bounded manual autoscan test and show exact post/block decisions.",
        when_to_use="Use only for diagnostics after a deploy or setup change. It is not required for normal background coverage.",
        credit_risk="Walmart official API",
    ),
    CommandCatalogEntry(
        name="/walmart categories",
        audience="Owner",
        purpose="Boost, normalize, or mute categories for this server's public feed.",
        when_to_use="Use to customize delivery after the normal Best Setup has been applied.",
    ),
    CommandCatalogEntry(
        name="/walmart threshold",
        audience="Owner",
        purpose="Set this server's minimum exact Walmart markdown.",
        when_to_use="Use 30-40% for normal coverage or 50%+ for stricter alerts. It filters delivery, not global discovery.",
    ),
    CommandCatalogEntry(
        name="/walmart extreme",
        audience="Owner",
        purpose="Turn exact-price Walmart leads with no verified original price on or off.",
        when_to_use="Use to opt this server into (or out of) separately labeled exact-price leads up to $10.",
    ),
    CommandCatalogEntry(
        name="/walmart active",
        audience="Owner",
        purpose="Browse recently observed public-quality deals and recheck exact Walmart items from the page controls.",
        when_to_use="Use for cache review, single-item rechecks, or bounded page rechecks. Separate recheck and cleanup aliases were retired.",
    ),
    CommandCatalogEntry(
        name="/walmart history",
        audience="Owner",
        purpose="Review durable lifecycle changes and the Walmart recheck audit.",
        when_to_use="Use when you need historical evidence instead of current active rows.",
    ),
    CommandCatalogEntry(
        name="/walmart trace",
        audience="Owner",
        purpose="Read-only evidence trace for one exact Walmart item.",
        when_to_use="Use when a deal did not post and you need the queue, fanout, and latency evidence for one item ID.",
    ),
    CommandCatalogEntry(
        name="/walmart feedback",
        audience="Owner",
        purpose="Show what SniperPlug learned from deal feedback buttons.",
        when_to_use="Use to review boosted and penalized products and brands before changing thresholds.",
    ),
    CommandCatalogEntry(
        name="/walmart feedback-reset",
        audience="Owner",
        purpose="Reset SniperPlug feedback learning for this server.",
        when_to_use="Use only when learning went sideways; pick a scope and confirm.",
    ),
    CommandCatalogEntry(
        name="/sniperplug …",
        audience="Owner",
        purpose="Owner-only grouped diagnostics such as provider previews, routes, monitor plans, and test alerts.",
        when_to_use="Use only for technical diagnostics. Normal users should use `/walmart find`, `/walmart hunt`, `/walmart cash`, or `/walmart my-alerts`.",
    ),
)


COMMAND_AUDIENCE_ORDER = ("Everyone", "Staff", "Owner")


def entries_for_audience(
    audience: str | None = None,
) -> tuple[CommandCatalogEntry, ...]:
    if not audience:
        return COMMAND_CATALOG
    normalized = audience.strip().lower()
    return tuple(
        entry
        for entry in COMMAND_CATALOG
        if entry.audience.lower() == normalized
    )


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
        command_name = entry.name[1:].split()[0].strip()
        if command_name in RETIRED_COMMAND_NAMES:
            errors.append(
                f"Retired command advertised in canonical catalog: {entry.name}"
            )
    return errors
