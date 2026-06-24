from pathlib import Path


DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")


def test_libsql_execute_eager_fetches_result_rows():
    assert "_LibsqlAsyncCursor.from_result(result)" in DB
    assert "return _LibsqlAsyncCursor(result, self._lock)" not in DB
    assert "consume result rows immediately" in DB


def test_libsql_reconnects_on_hrana_stream_errors():
    assert "stream not found" in DB
    assert "stream already in use" in DB
    assert "_reconnect_sync" in DB
    assert "database=turso_url, auth_token=turso_token" in DB


def test_autoscan_loop_survives_one_guild_failure():
    assert "Auto-scan guild run failed but loop will continue" in AUTO
    assert "except Exception" in AUTO
