from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_autoscan_now_has_fast_pass_and_deep_followup():
    assert "Fast pass result" in AUTO
    assert "Deep follow-up result" in AUTO
    assert "query_count_override=AUTO_SCAN_DEEP_QUERY_COUNT if force else None" in AUTO
    assert "query_count_override=AUTO_SCAN_MANUAL_QUERY_COUNT" in AUTO


def test_autoscan_sends_progress_notice_while_waiting():
    assert "AUTO_SCAN_PROGRESS_SECONDS" in AUTO
    assert "async def _autoscan_progress_notice" in AUTO
    assert "Still scanning Walmart" in AUTO


def test_autoscan_query_count_override_is_supported():
    assert "query_count_override: int | None = None" in AUTO
    assert "if query_count_override is not None" in AUTO
    assert "report_label" in AUTO
