from __future__ import annotations

from pathlib import Path

AUTO = Path("sniperplug/cogs/auto_scan_runner.py")
TEST = Path("tests/test_autoscan_observed_memory_wiring.py")
ACTIVE_TASK = Path("ACTIVE_TASK.md")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    source = AUTO.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "from sniperplug.services.autoscan_history import save_autoscan_report\n",
        "from sniperplug.services.autoscan_history import save_autoscan_report\n"
        "from sniperplug.services.autoscan_observed_price_memory import run_autoscan_verified_category_with_observed_memory\n",
        "observed memory import",
    )
    source = replace_once(
        source,
        '''async def run_autoscan_verified_category(db, guild_id: int, *, preset: HuntPreset) -> VerifiedHuntResult:\n    return await collect_verified_discount_cards(\n        requested_by="autoscan",\n        preset=preset,\n        db=db,\n        guild_id=guild_id,\n        use_price_memory=False,\n    )\n''',
        '''async def run_autoscan_verified_category(db, guild_id: int, *, preset: HuntPreset) -> VerifiedHuntResult:\n    """Run the bounded autoscan collector while retaining exact-item price observations.\n\n    Scheduled scans stay lightweight (two pages per route, bounded concurrency and\n    capped observation writes), but they must keep trustworthy historical prices.\n    Otherwise every scan starts from zero and observed-price-drop proof can never\n    mature into a verified public deal.\n    """\n    return await run_autoscan_verified_category_with_observed_memory(\n        db,\n        guild_id,\n        preset=preset,\n    )\n''',
        "autoscan collector delegation",
    )
    if "use_price_memory=False" in source:
        raise SystemExit("autoscan still disables price memory")
    AUTO.write_text(source, encoding="utf-8")

    TEST.write_text(
        '''from pathlib import Path\n\n\nROOT = Path(__file__).resolve().parents[1]\nAUTO = (ROOT / "sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")\nMEMORY = (ROOT / "sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")\n\n\ndef test_autoscan_uses_bounded_observed_price_memory_collector() -> None:\n    assert "run_autoscan_verified_category_with_observed_memory" in AUTO\n    assert "use_price_memory=False" not in AUTO\n    assert "AUTOSCAN_PAGES_PER_QUERY = 2" in MEMORY\n    assert "AUTOSCAN_SEARCH_CONCURRENCY = 3" in MEMORY\n    assert "AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES = 300" in MEMORY\n\n\ndef test_observed_memory_can_produce_verified_drop_cards() -> None:\n    assert "select_observed_price_drop_cards" in MEMORY\n    assert "use_price_memory=True" in MEMORY\n    assert "cards = rank_verified_cards(memory_cards)" in MEMORY\n''',
        encoding="utf-8",
    )

    ACTIVE_TASK.write_text(
        '''# Active Task\n\n## Status\nIn progress — make scheduled Walmart scans retain bounded observed-price history so verified price-drop deals can mature and post.\n\n## Scope\nRepair the authoritative autoscan collector only. Keep the strict verified public threshold, four scheduled routes, eight manual routes, bounded provider concurrency, and one scheduled guild scan at a time.\n\n## Root cause\n- The live autoscan path called `collect_verified_discount_cards(... use_price_memory=False)`.\n- Logs therefore reported `price_memory_summary: not used` after every scan.\n- Walmart frequently omits trustworthy reference prices, so strict verification rejected nearly every product.\n- Because scheduled scans discarded observations, SniperPlug could never build its own exact-item historical baseline and later prove a real price drop.\n\n## Changes\n- Delegate the authoritative autoscan collector to the existing bounded observed-memory service.\n- Preserve two pages per route, concurrency three inside the provider collector, a four-item memory recheck seed limit, and a 300-observation write cap per pass.\n- Keep all public-deal quality gates unchanged.\n- Add structural regressions forbidding `use_price_memory=False` on autoscan.\n\n## Validation required\n- Compile changed runtime and tests.\n- Run targeted observed-memory and autoscan tests.\n- Run import smoke.\n- Run complete pytest suite.\n- Remove temporary applicator/workflow and inspect final diff before merge.\n\n## Cleanup status\nPending.\n\n## Blockers\nNone.\n\n## Backlog\n- Surface observation baseline counts and time-to-maturity in `/autoscan_health` after this fix is deployed.\n''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
