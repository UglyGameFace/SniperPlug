from pathlib import Path

DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")
SCANNER = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
CASH = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")

def test_db_serializes_turso_stream_calls():
    assert "_LIBSQL_OPERATION_LOCK" in DB
    assert "stream already in use" in DB
    assert "stream not found" in DB
    assert "for attempt in range(3)" in DB
    assert "async with lock" in DB

def test_walmart_cash_search_is_not_heavy_fanout():
    assert "asyncio.Semaphore(2)" in SCANNER
    assert "queries[:3]" in SCANNER
    assert "for page in (1,)" in SCANNER or "for page in [1]" in SCANNER

def test_walmart_cash_zero_result_message_distinguishes_timeout():
    assert "from returned API results" in CASH
    assert "Checked: 0" in CASH
    assert "before timeout" in CASH
