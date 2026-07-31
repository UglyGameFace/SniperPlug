from __future__ import annotations

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
    assert SOURCE.count('report_label="Manual pass"') == 1
    assert 'label="Manual scan result"' in SOURCE
    assert "asyncio.wait_for(" not in SOURCE
