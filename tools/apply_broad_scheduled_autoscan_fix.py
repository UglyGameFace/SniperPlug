from __future__ import annotations

from pathlib import Path


NATIVE = Path("sniperplug/cogs/native_auto_scan_runner.py")
BOT = Path("sniperplug/bot.py")
TEST = Path("tests/test_native_autoscan_selection_static.py")
ACTIVE_TASK = Path("ACTIVE_TASK.md")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    native = NATIVE.read_text(encoding="utf-8")
    native = replace_once(
        native,
        'warnings.append("Manual broad sweep spans the major public-safe categories instead of staying inside one category.")',
        'warnings.append("Broad sweep spans the major public-safe categories instead of staying inside one category.")',
        "broad warning copy",
    )
    native = replace_once(
        native,
        '''            else NATIVE_MANUAL_QUERY_COUNT if force else legacy.AUTO_SCAN_FAST_QUERY_COUNT
        ),
    )
    if force:
        return build_native_broad_preset(presets, guild_id=guild_id, query_count=query_count)

    bucket = int(time.time() // (legacy.AUTO_SCAN_INTERVAL_MINUTES * 60))
    key = NATIVE_CATEGORY_ROTATION[(bucket + int(guild_id)) % len(NATIVE_CATEGORY_ROTATION)]
    base = presets.get(key) or presets.get("deal_week") or presets.get("all") or next(iter(presets.values()))
    queries = legacy.rotated_query_slice(tuple(base.queries), guild_id=guild_id, query_count=query_count)
    return HuntPreset(
        base.key,
        base.label,
        base.emoji,
        f"{base.description} Native autoscan uses public-safe routes and verified-only public posting.",
        queries,
        base.min_discount,
    )
''',
        '''            else NATIVE_MANUAL_QUERY_COUNT if force else legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT
        ),
    )
    return build_native_broad_preset(presets, guild_id=guild_id, query_count=query_count)
''',
        "native scheduled selector",
    )
    native = replace_once(
        native,
        '"Manual broad sweep across the major public-safe Walmart categories, with private promo routes removed before scanning.",',
        '"Broad sweep across the major public-safe Walmart categories, with private promo routes removed before scanning.",',
        "broad preset description",
    )
    if "AUTO_SCAN_FAST_QUERY_COUNT" in native:
        raise SystemExit("removed autoscan fast-policy reference still remains in native runner")
    NATIVE.write_text(native, encoding="utf-8")

    bot = BOT.read_text(encoding="utf-8")
    bot = replace_once(
        bot,
        "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\n",
        "",
        "unused native runner import",
    )
    BOT.write_text(bot, encoding="utf-8")

    test = TEST.read_text(encoding="utf-8")
    test = replace_once(
        test,
        '    assert "Manual broad sweep spans" in source\n',
        '    assert "Broad sweep spans" in source\n',
        "broad warning assertion",
    )
    test += '''\n\ndef test_scheduled_native_autoscan_spreads_bounded_routes_across_categories() -> None:\n    source = read("sniperplug/cogs/native_auto_scan_runner.py")\n    assert "legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT" in source\n    assert "legacy.AUTO_SCAN_FAST_QUERY_COUNT" not in source\n    assert source.count("return build_native_broad_preset(presets, guild_id=guild_id, query_count=query_count)") == 1\n    assert "bucket = int(time.time()" not in source\n\n\ndef test_only_resilient_autoscan_runner_is_imported_by_runtime() -> None:\n    source = read("sniperplug/bot.py")\n    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\\n" not in source\n    assert "from sniperplug.cogs.resilient_auto_scan_runner import AutoScanRunnerCog as ResilientAutoScanRunnerCog" in source\n    assert source.count("await self.add_cog(ResilientAutoScanRunnerCog(self))") == 1\n'''
    TEST.write_text(test, encoding="utf-8")

    ACTIVE_TASK.write_text(
        '''# Active Task\n\n## Status\nIn progress — restore useful scheduled Walmart autoscan coverage without loosening verified-deal safety.\n\n## Scope\nTrace and repair the live resilient autoscan path responsible for configured servers receiving no public posts. Keep one active runtime, four scheduled routes, eight manual routes, one provider scan at a time, and verified-only public posting.\n\n## Findings\n- The runtime loads `ResilientAutoScanRunnerCog`, which inherits the native runner.\n- Scheduled scans explicitly cap work at four routes.\n- The native selector spent all four scheduled routes inside one rotated category, then recorded an empty pass and waited behind the six-hour safety floor.\n- The native fallback still referenced deleted `AUTO_SCAN_FAST_QUERY_COUNT`.\n- `bot.py` retained an unused direct native runner import even though only the resilient runner is registered.\n\n## Changes\n- Scheduled four-route scans now use the existing broad public-safe builder, selecting one route across multiple major categories.\n- Manual eight-route scans continue using the same broad builder.\n- Replaced the deleted fast-policy fallback with `AUTO_SCAN_SCHEDULED_QUERY_COUNT`.\n- Removed the unused direct native runner import from `bot.py`.\n- Added cross-runner static regressions for broad scheduled coverage and one runtime import/registration.\n\n## Validation required\n- Compile changed runtime and tests.\n- Run targeted native/resilient autoscan tests.\n- Run import smoke validation.\n- Run complete pytest regression suite.\n- Inspect final diff for temporary files, stale policy names, duplicate runner wiring, and conflicts.\n\n## Cleanup status\nPending. Temporary applicator/workflow must be removed before merge.\n\n## Blockers\nNone.\n\n## Backlog\n- Improve scheduled zero-post diagnostics surfaced to server owners after this execution-path repair is validated.\n''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
