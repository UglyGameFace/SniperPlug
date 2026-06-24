from pathlib import Path
import re

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")


def _method_source(src: str, name: str) -> str:
    marker = f"async def {name}("
    start = src.index(marker)
    search_from = start + len(marker)

    candidates = []
    for token in (
        "\n    @app_commands.",
        "\n    @commands.",
        "\n    async def ",
        "\n    def ",
    ):
        pos = src.find(token, search_from)
        if pos != -1:
            candidates.append(pos)

    end = min(candidates) if candidates else len(src)
    return src[start:end]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


COMMAND = _method_source(DEALS, "walmart_cash")
HELPER = _method_source(DEALS, "_send_walmart_cash_search")
COMMAND_C = _compact(COMMAND)
HELPER_C = _compact(HELPER)


DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")


def test_cashfinder_is_fast_and_scoped_to_helper():
    assert "queries[:2]" in HELPER_C
    assert "asyncio.Semaphore(2)" in HELPER_C
    assert "timeout=8" in HELPER_C


def test_turso_stream_errors_have_retry_tokens():
    lower = DB.lower()
    assert "stream not found" in lower
    assert "stream already in use" in lower
    assert "_reconnect_sync" in DB
