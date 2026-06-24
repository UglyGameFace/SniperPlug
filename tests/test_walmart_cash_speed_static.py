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


def test_walmart_cash_speed_is_checked_on_helper_not_whole_file():
    assert "queries[:2]" in HELPER_C
    assert "asyncio.Semaphore(2)" in HELPER_C
    assert "timeout=8" in HELPER_C
    assert "per_route_limit=max(3,min(12,int(max_results)))" in HELPER_C
