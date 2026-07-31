from __future__ import annotations

from pathlib import Path


# One-time branch applicator. Removed before merge.
TARGET = Path("sniperplug/cogs/auto_scan_runner.py")
TEST = Path("tests/test_autoscan_policy_structure.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")

    old_constants = '''AUTO_SCAN_FAST_QUERY_COUNT = 8
AUTO_SCAN_DEEP_QUERY_COUNT = 16
AUTO_SCAN_MANUAL_QUERY_COUNT = 32
AUTO_SCAN_DEEP_EVERY_BUCKETS = 8
AUTO_SCAN_PROGRESS_SECONDS = 45
AUTO_SCAN_DEEP_FOLLOWUP_ENABLED = True
AUTO_SCAN_GUILD_TIMEOUT_SECONDS = 180
AUTO_SCAN_MAX_CONCURRENCY = 3
AUTO_SCAN_GUILD_TIMEOUT_SECONDS = 180
AUTO_SCAN_MAX_CONCURRENCY = 3
AUTO_SCAN_GUILD_TIMEOUT_SECONDS = 180
AUTO_SCAN_MAX_CONCURRENCY = 3
'''
    new_constants = '''AUTO_SCAN_SCHEDULED_QUERY_COUNT = 4
AUTO_SCAN_MANUAL_QUERY_COUNT = 8
AUTO_SCAN_PROGRESS_SECONDS = 45
AUTO_SCAN_MAX_CONCURRENCY = 1
'''
    text = replace_once(text, old_constants, new_constants, "autoscan constant block")

    old_manual = '''                progress_task = asyncio.create_task(self._autoscan_progress_notice(interaction))
                try:
                    report = await self._run_guild_walmart_discovery(
                        AutoScanGuild(guild_id, config.get("channel_id")),
                        force=force,
                        query_count_override=AUTO_SCAN_DEEP_QUERY_COUNT if force else None,
                        report_label="Fast pass",
                    )
                finally:
                    progress_task.cancel()
                await self._send_autoscan_report(interaction, report, label="Fast pass result")

                if force and AUTO_SCAN_DEEP_FOLLOWUP_ENABLED:
                    await self._safe_autoscan_followup(
                        interaction,
                        "🔎 Fast pass finished. I’m continuing a deeper Walmart scan in the background and will send a second report if it finds anything different.",
                    )
                    deep_report = await self._run_guild_walmart_discovery(
                        AutoScanGuild(guild_id, config.get("channel_id")),
                        force=force,
                        query_count_override=AUTO_SCAN_MANUAL_QUERY_COUNT,
                        report_label="Deep follow-up",
                    )
                    await self._send_autoscan_report(interaction, deep_report, label="Deep follow-up result")
'''
    new_manual = '''                progress_task = asyncio.create_task(self._autoscan_progress_notice(interaction))
                try:
                    report = await self._run_guild_walmart_discovery(
                        AutoScanGuild(guild_id, config.get("channel_id")),
                        force=force,
                        query_count_override=AUTO_SCAN_MANUAL_QUERY_COUNT if force else None,
                        report_label="Manual pass",
                    )
                finally:
                    progress_task.cancel()
                await self._send_autoscan_report(interaction, report, label="Manual scan result")
'''
    text = replace_once(text, old_manual, new_manual, "manual fast/deep flow")

    old_scheduled = '''            async with lock:
                try:
                    await asyncio.wait_for(
                        self._run_guild_walmart_discovery(guild),
                        timeout=AUTO_SCAN_GUILD_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    reason = (
                        f"Auto-scan timed out after {AUTO_SCAN_GUILD_TIMEOUT_SECONDS} seconds. "
                        "This guild was isolated so other servers could continue scanning."
                    )
                    report = AutoScanReport(
                        guild_id=guild.guild_id,
                        allowed=False,
                        reason=reason,
                        settings={
                            "timeout_seconds": AUTO_SCAN_GUILD_TIMEOUT_SECONDS,
                            "isolated": True,
                            "retailer": AUTO_SCAN_RETAILER,
                        },
                    )
                    await persist_autoscan_report(self.bot.db, report, scan_key=AUTO_SCAN_SOURCE_LABEL)
                    log.error("Auto-scan guild timed out and was isolated guild=%s timeout_s=%s", guild.guild_id, AUTO_SCAN_GUILD_TIMEOUT_SECONDS)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Auto-scan guild run failed but loop will continue; other guild tasks are isolated guild=%s", guild.guild_id)
'''
    new_scheduled = '''            async with lock:
                try:
                    await self._run_guild_walmart_discovery(guild)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Auto-scan guild run failed but loop will continue; other guild tasks are isolated guild=%s", guild.guild_id)
'''
    text = replace_once(text, old_scheduled, new_scheduled, "scheduled whole-pass timeout")

    old_query_policy = '''    if query_count_override is not None:
        query_count = max(1, int(query_count_override))
    elif force:
        query_count = AUTO_SCAN_MANUAL_QUERY_COUNT
    else:
        bucket = int(time.time() // (AUTO_SCAN_INTERVAL_MINUTES * 60))
        query_count = AUTO_SCAN_DEEP_QUERY_COUNT if bucket % AUTO_SCAN_DEEP_EVERY_BUCKETS == 0 else AUTO_SCAN_FAST_QUERY_COUNT
'''
    new_query_policy = '''    if query_count_override is not None:
        query_count = max(1, int(query_count_override))
    elif force:
        query_count = AUTO_SCAN_MANUAL_QUERY_COUNT
    else:
        query_count = AUTO_SCAN_SCHEDULED_QUERY_COUNT
'''
    text = replace_once(text, old_query_policy, new_query_policy, "query-count policy")

    forbidden = (
        "AUTO_SCAN_FAST_QUERY_COUNT",
        "AUTO_SCAN_DEEP_QUERY_COUNT",
        "AUTO_SCAN_DEEP_EVERY_BUCKETS",
        "AUTO_SCAN_DEEP_FOLLOWUP_ENABLED",
        "AUTO_SCAN_GUILD_TIMEOUT_SECONDS",
        "Deep follow-up",
        "asyncio.wait_for(\n                        self._run_guild_walmart_discovery",
    )
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise SystemExit(f"forbidden legacy autoscan policy remains: {leftovers}")

    TARGET.write_text(text, encoding="utf-8")
    TEST.write_text(
        '''from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "sniperplug/cogs/auto_scan_runner.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


def _top_level_assignments() -> Counter[str]:
    tree = ast.parse(SOURCE)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
    return Counter(names)


def test_autoscan_policy_names_are_not_reassigned() -> None:
    duplicates = {
        name: count
        for name, count in _top_level_assignments().items()
        if count > 1 and (name.startswith("AUTO_SCAN_") or name.startswith("AUTOSCAN_"))
    }
    assert duplicates == {}


def test_autoscan_has_one_bounded_route_policy() -> None:
    assert "AUTO_SCAN_SCHEDULED_QUERY_COUNT = 4" in SOURCE
    assert "AUTO_SCAN_MANUAL_QUERY_COUNT = 8" in SOURCE
    assert "AUTO_SCAN_MAX_CONCURRENCY = 1" in SOURCE
    for legacy in (
        "AUTO_SCAN_FAST_QUERY_COUNT",
        "AUTO_SCAN_DEEP_QUERY_COUNT",
        "AUTO_SCAN_DEEP_EVERY_BUCKETS",
        "AUTO_SCAN_DEEP_FOLLOWUP_ENABLED",
        "AUTO_SCAN_GUILD_TIMEOUT_SECONDS",
        "Deep follow-up",
    ):
        assert legacy not in SOURCE


def test_manual_and_scheduled_scans_are_single_pass() -> None:
    assert SOURCE.count("report_label=\"Manual pass\"") == 1
    assert "label=\"Manual scan result\"" in SOURCE
    assert "asyncio.wait_for(\n                        self._run_guild_walmart_discovery" not in SOURCE
''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
