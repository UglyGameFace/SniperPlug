from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"missing target in {relative}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_autoscan_runtime_diagnostics.py",
    '''    async def execute(self, _sql, _params=()):\n        return _Cursor((455, 0, 0, 0, 310, 290, 120, 0, 25, 10, 5))\n''',
    '''    async def execute(self, _sql, _params=()):\n        return _Cursor((455, 0, 0, 0, 0, 310, 120, 290, 120, 0, 25, 10, 5))\n''',
)
replace_once(
    "tests/test_autoscan_runtime_diagnostics.py",
    '''    assert health.initial_due_now == 0\n    assert health.recheck_due_now == 0\n    assert health.delayed_retries == 310\n    assert health.identity_blocked == 290\n''',
    '''    assert health.initial_due_now == 0\n    assert health.recovery_due_now == 0\n    assert health.recheck_due_now == 0\n    assert health.delayed_retries == 310\n    assert health.delayed_recovery_retries == 120\n    assert health.identity_blocked == 290\n''',
)
replace_once(
    "tests/test_autoscan_runtime_diagnostics.py",
    '''    assert "new/retry **0**" in health.summary_line()\n    assert "scheduled rechecks **0**" in health.summary_line()\n    assert "delayed transient retries **310**" in health.summary_line()\n''',
    '''    assert "new/retry **0**" in health.summary_line()\n    assert "page recovery **0**" in health.summary_line()\n    assert "scheduled rechecks **0**" in health.summary_line()\n    assert "delayed transient retries **310**" in health.summary_line()\n    assert "page recovery **120**" in health.summary_line()\n''',
)

replace_once(
    "tests/test_global_autoscan_architecture.py",
    '''            "initial_due_now": 1,\n            "recheck_due_now": 2,\n            "delayed_retries": 1,\n''',
    '''            "initial_due_now": 1,\n            "recovery_due_now": 0,\n            "recheck_due_now": 2,\n            "delayed_retries": 1,\n            "delayed_recovery_retries": 0,\n''',
)
replace_once(
    "tests/test_global_autoscan_architecture.py",
    '''    assert health.initial_due_now == 1\n    assert health.recheck_due_now == 2\n    assert health.verifying == 1\n''',
    '''    assert health.initial_due_now == 1\n    assert health.recovery_due_now == 0\n    assert health.recheck_due_now == 2\n    assert health.verifying == 1\n''',
)
replace_once(
    "tests/test_global_autoscan_architecture.py",
    '''    assert len(conn.params) == 9\n''',
    '''    assert len(conn.params) == 12\n''',
)

replace_once(
    "tests/test_walmart_exact_runtime_schema.py",
    '''        assert first_conn.execute_count == 2\n        assert first_conn.commit_count == 1\n        assert second_conn.execute_count == 2\n        assert second_conn.commit_count == 1\n        claim_sql, pressure_sql = first_conn.sql\n''',
    '''        assert first_conn.execute_count == 3\n        assert first_conn.commit_count == 1\n        assert second_conn.execute_count == 3\n        assert second_conn.commit_count == 1\n        drop_legacy_sql, claim_sql, pressure_sql = first_conn.sql\n        assert runtime_schema.LEGACY_CLAIM_ORDER_INDEX in drop_legacy_sql\n        assert "DROP INDEX IF EXISTS" in drop_legacy_sql\n''',
)
replace_once(
    "tests/test_walmart_exact_runtime_schema.py",
    '''        "WHEN 'pending' THEN 0",\n        "WHEN 'verified_markdown' THEN 4",\n        "priority_score DESC",\n''',
    '''        "WHEN 'pending' THEN 0",\n        "WHEN 'page_retry' THEN 3",\n        "WHEN 'verified_markdown' THEN 5",\n        "priority_score DESC",\n''',
)

# Record that canonical full-suite regressions were inspected and were stale test contracts.
path = ROOT / "ACTIVE_TASK.md"
text = path.read_text(encoding="utf-8")
needle = "## Validation\n- Implementation patch applied on `fix/walmart-page-retry-fanout`; targeted and full validation pending canonical CI."
replacement = "## Validation\n- Implementation patch applied on `fix/walmart-page-retry-fanout`; focused compile/diff/tests passed.\n- First canonical PR run: import smoke **PASS**; full suites exposed four stale architecture assertions that still modeled the pre-`page_retry` health tuple/SQL parameter count/index statement count/claim ordinal. Runtime behavior was inspected before updating those contracts.\n- Canonical full-suite rerun pending after test-contract repair."
if needle not in text:
    raise RuntimeError("ACTIVE_TASK validation marker missing")
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")

# Self-clean temporary applicator artifacts before the commit.
for relative in (
    ".github/scripts/apply_walmart_retry_test_contracts.py",
    ".github/workflows/apply-walmart-retry-test-contracts.yml",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()

print("Updated Walmart retry regression contracts")
