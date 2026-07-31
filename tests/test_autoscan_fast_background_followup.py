from pathlib import Path


AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
NATIVE = Path("sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
RESILIENT = Path("sniperplug/cogs/resilient_auto_scan_runner.py").read_text(encoding="utf-8")


def test_autoscan_now_is_one_bounded_manual_pass():
    assert 'label="Manual scan result"' in AUTO
    assert 'report_label="Manual pass"' in AUTO
    assert "Fast pass result" not in AUTO
    assert "Deep follow-up result" not in AUTO
    assert "AUTO_SCAN_DEEP_QUERY_COUNT" not in AUTO
    assert "query_count_override=8" in RESILIENT


def test_autoscan_sends_repeating_progress_notices_while_waiting():
    assert "MANUAL_PROGRESS_INTERVAL_SECONDS = 45" in RESILIENT
    assert "while True:" in RESILIENT
    assert "Walmart is still responding and the scan remains active" in RESILIENT
    assert "Progress update #" in RESILIENT


def test_autoscan_query_count_override_is_supported():
    assert "query_count_override: int | None = None" in AUTO
    assert "if query_count_override is not None" in AUTO
    assert "query_count_override: int | None = None" in NATIVE
    assert "report_label" in AUTO
    assert "report_label" in NATIVE
