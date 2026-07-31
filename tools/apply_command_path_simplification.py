from __future__ import annotations

from pathlib import Path

WORKFLOW = Path("sniperplug/cogs/workflow.py")
DASHBOARD = Path("sniperplug/cogs/settings_dashboard.py")
CATALOG = Path("sniperplug/services/command_catalog.py")
TEST = Path("tests/test_command_path_simplification.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"{label} not found")
    return text.replace(old, new, 1)


def patch_workflow() -> None:
    text = WORKFLOW.read_text()
    old = '''        embed = discord.Embed(
            title="SniperPlug Workflow",
            description="Use this order so the bot feels simple instead of scattered.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="1. Setup once", value="Run `/setup_sniperplug_here` inside the deal channel. It sets the public alert route, retailers, and Walmart background auto-scan together.", inline=False)
        embed.add_field(name="2. Manual testing", value="Use `/deals` for one item, `/hunt` for category buttons, or `/discover` for broad manual discovery. Manual scans do not depend on auto-scan being enabled.", inline=False)
        embed.add_field(name="3. Background scanning", value="Use `/retailer_autoscan` when you want to change scheduled/background pulls. Paid-credit providers stay protected; Walmart can run unlimited through official-provider bypass.", inline=False)
        embed.add_field(name="4. Troubleshooting", value="Use `/autoscan_health`, `/sniperplug_dashboard`, `/active_deals`, and `/sniperplug_commands` to see what is configured, cached, and available.", inline=False)
        embed.set_footer(text="Public posting requires public alerts ON, an alert channel, allowed retailers, and alertable proof.")
'''
    new = '''        embed = discord.Embed(
            title="SniperPlug • Start Here",
            description=(
                "You only need **three main search paths**. Pick the one that matches what you are trying to do; "
                "the other commands are specialist or owner tools."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="🔎 I know the product",
            value="Use **`/deals`** and type the product words. Example: `turtle wax`, `gaming headset`, or `lego`.",
            inline=False,
        )
        embed.add_field(
            name="🎯 I want category buttons",
            value="Use **`/hunt`** when you want SniperPlug to search preset categories and resale/value lanes for you.",
            inline=False,
        )
        embed.add_field(
            name="📰 Scan broadly right now",
            value="Use **`/discover`** for a wider newspaper-style manual discovery run across deal categories.",
            inline=False,
        )
        embed.add_field(
            name="Special searches",
            value=(
                "Use **`/walmart_cash`** only for proven Walmart Cash offers. "
                "Use the **Home Depot commands** for private shopper leads and exact-store verification."
            ),
            inline=False,
        )
        embed.add_field(
            name="Owner setup and troubleshooting",
            value=(
                "Run **`/setup_sniperplug_here`** once in the posting channel. "
                "Use **`/sniperplug_dashboard`** for the normal status view and **`/sniperplug_doctor`** after deploys. "
                "Advanced raw controls stay in `/sniperplug_commands audience:owner`."
            ),
            inline=False,
        )
        embed.set_footer(text="Manual searches work even when scheduled auto-scan is off. Start with /deals, /hunt, or /discover.")
'''
    WORKFLOW.write_text(replace_once(text, old, new, "workflow start-here embed"))


def patch_catalog() -> None:
    text = CATALOG.read_text()
    text = text.replace(
        'purpose="Show the simple SniperPlug workflow from setup to posting.",\n        when_to_use="Use when anyone is confused about what to run next.",',
        'purpose="Show the three primary search paths and separate normal commands from specialist/owner tools.",\n        when_to_use="Use this first when anyone is unsure whether to run `/deals`, `/hunt`, or `/discover`.",',
    )
    text = text.replace(
        'purpose="Advanced Walmart scan with page, sort, discount, and alert-only controls.",\n        when_to_use="Use for deeper staff testing when `/deals` is too simple.",',
        'purpose="Advanced Walmart diagnostic scan with page, sort, discount, and alert-only controls.",\n        when_to_use="Power-user tool. Normal users should start with `/deals`; use this only when exact scan controls are required.",',
    )
    text = text.replace(
        'purpose="Show the command guide.",\n        when_to_use="Use when anyone is confused about which SniperPlug command does what.",',
        'purpose="Show the full command reference grouped by primary, specialist, owner, and advanced paths.",\n        when_to_use="Use after `/sniperplug_workflow` when you need a specialist or diagnostic command.",',
    )
    CATALOG.write_text(text)


def patch_dashboard() -> None:
    text = DASHBOARD.read_text()
    old = '''def build_command_guide_embed(entries: tuple[CommandCatalogEntry, ...], audience: str | None = None) -> discord.Embed:
    title = "SniperPlug Command Guide"
    description = "Simple names, clear purpose. Manual scans are different from scheduled auto-scan. Public posting is different from auto-scan."
    if audience:
        description += f"\\nFiltered to: **{audience.title()}**"
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
            lines.append(f"**{entry.name}** — {entry.purpose}\\nUse when: {entry.when_to_use}{credit}")
        embed.add_field(name=group_name, value=truncate("\\n\\n".join(lines), 1024), inline=False)

    embed.set_footer(text="Owner tip: use /sniperplug_dashboard when something feels wrong.")
    return embed
'''
    new = '''PRIMARY_COMMANDS = {"/deals", "/hunt", "/discover"}
SPECIALIST_COMMANDS = {
    "/walmart_cash",
    "/home_depot_search",
    "/home_depot_penny_hunt",
    "/hd_stock",
    "/hd_penny_zip",
    "/local_check",
    "/seed_clearance",
    "/clearance_bank",
}
ADVANCED_COMMANDS = {
    "/walmart_scan",
    "/active_deals_cleanup",
    "/public_alerts_status",
    "/setup_sniperplug_here_status",
    "/retailer_autoscan",
    "/retailer_autoscan_status",
    "/sniperplug_health",
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
        "Start with **`/deals`**, **`/hunt`**, or **`/discover`**. "
        "Specialist and diagnostic commands are grouped separately so you do not have to understand the whole bot first."
    )
    if audience:
        description += f"\\nFiltered to: **{audience.title()}**"
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
            lines.append(f"**{entry.name}** — {entry.purpose}\\nUse when: {entry.when_to_use}{credit}")
        embed.add_field(name=section, value=truncate("\\n\\n".join(lines), 1024), inline=False)

    embed.set_footer(text="Not sure? Run /sniperplug_workflow. Owners: use /sniperplug_dashboard when something feels wrong.")
    return embed
'''
    DASHBOARD.write_text(replace_once(text, old, new, "command guide grouping"))


def write_tests() -> None:
    TEST.write_text('''from pathlib import Path\n\nfrom sniperplug.cogs.settings_dashboard import command_guide_section\nfrom sniperplug.services.command_catalog import COMMAND_CATALOG\n\n\ndef entry(name):\n    return next(item for item in COMMAND_CATALOG if item.name == name)\n\n\ndef test_three_primary_paths_are_explicit():\n    assert command_guide_section(entry("/deals")) == "Start here"\n    assert command_guide_section(entry("/hunt")) == "Start here"\n    assert command_guide_section(entry("/discover")) == "Start here"\n\n\ndef test_advanced_walmart_scan_is_not_presented_as_starting_path():\n    assert command_guide_section(entry("/walmart_scan")) == "Advanced / diagnostic"\n    assert "Normal users should start with `/deals`" in entry("/walmart_scan").when_to_use\n\n\ndef test_workflow_copy_names_only_three_main_search_paths():\n    source = Path("sniperplug/cogs/workflow.py").read_text()\n    assert "three main search paths" in source\n    assert "Start with /deals, /hunt, or /discover" in source\n    assert "Advanced raw controls" in source\n\n\ndef test_specialist_commands_are_separated():\n    assert command_guide_section(entry("/walmart_cash")) == "Special searches"\n    assert command_guide_section(entry("/hd_stock")) == "Special searches"\n''')


def main() -> None:
    patch_workflow()
    patch_catalog()
    patch_dashboard()
    write_tests()


if __name__ == "__main__":
    main()
