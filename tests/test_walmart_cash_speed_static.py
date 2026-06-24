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


def test_walmart_cash_command_uses_fast_bounded_routes():
    assert "queries[:2]" in HELPER
    assert "for page in (1,)" in HELPER
    assert "per_route_limit = max(3, min(12, int(max_results)))" in HELPER
    assert "asyncio.Semaphore(2)" in HELPER
    assert "timeout=8" not in HELPER_C


def test_walmart_cash_summary_reports_checked_routes_not_generated_routes():
    assert "used_queries = tuple(query for query, _page in scan_jobs)" in HELPER
    assert "build_walmart_cash_summary_embed(search, used_queries" in HELPER
    assert "Search routes actually checked" in OFFERS
