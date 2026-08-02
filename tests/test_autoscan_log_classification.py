from types import SimpleNamespace

from sniperplug.cogs import resilient_auto_scan_runner as runtime


def test_normal_scan_diagnostics_log_as_notes_not_warnings() -> None:
    assert runtime._is_actionable_autoscan_warning(
        "Catalog-wide route rotation: slot 4/33; full route pool 263"
    ) is False
    assert runtime._is_actionable_autoscan_warning(
        "Walmart exact-detail queue: discovered 291; due/pending 334"
    ) is False
    assert runtime._is_actionable_autoscan_warning(
        "Walmart API HTTP 500: temporary failure"
    ) is True


def test_queue_summary_does_not_double_count_identity_blocks_as_failures() -> None:
    result = SimpleNamespace(
        claimed=6,
        verified=1,
        official_references=1,
        markdowns=1,
        no_reference=0,
        under_threshold=0,
        unavailable=0,
        identity_blocked=5,
        failed=5,
        pending_total=192,
    )

    summary = runtime._walmart_queue_batch_summary(result)

    assert "identity unavailable / safely blocked **5**" in summary
    assert "transient failures **0**" in summary
    assert "failed/retrying" not in summary


def test_queue_summary_preserves_real_transient_failure_count() -> None:
    result = SimpleNamespace(
        claimed=6,
        verified=1,
        official_references=1,
        markdowns=0,
        no_reference=0,
        under_threshold=0,
        unavailable=0,
        identity_blocked=3,
        failed=5,
        pending_total=100,
    )

    summary = runtime._walmart_queue_batch_summary(result)

    assert "identity unavailable / safely blocked **3**" in summary
    assert "transient failures **2**" in summary
