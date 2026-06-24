from pathlib import Path
import re

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
CASH_TRUTH = Path("sniperplug/services/walmart_cash_api_truth.py").read_text(encoding="utf-8")


def method_source(src: str, name: str) -> str:
    lines = src.splitlines(keepends=True)
    marker = f"    async def {name}("
    start = next(i for i, line in enumerate(lines) if line.startswith(marker))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if (
            line.startswith("    @app_commands.")
            or line.startswith("    @commands.")
            or line.startswith("    async def ")
            or line.startswith("    def ")
        ):
            end = i
            break
    return "".join(lines[start:end])


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


HELPER = method_source(DEALS, "_send_walmart_cash_search")
HELPER_C = compact(HELPER)


DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")


def test_cashfinder_is_fast_and_scoped_to_helper():
    assert "queries[:2]" in HELPER
    assert "asyncio.Semaphore(2)" in HELPER
    assert "cash_route_timeout" in HELPER
    assert "timeout=8" not in HELPER_C
    assert "timeout=15" not in HELPER_C
    assert "HuntPresetMenuView" not in HELPER


def test_turso_stream_errors_have_retry_tokens():
    lower = DB.lower()
    assert "stream not found" in lower
    assert "stream already in use" in lower
    assert "_reconnect_sync" in DB
