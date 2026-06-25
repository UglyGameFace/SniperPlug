from pathlib import Path
import re

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


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


HELPER = method_source(DEALS, "_send_walmart_cash_search")
HELPER_C = re.sub(r"\s+", "", HELPER)


def test_cashfinder_uses_cash_discovery_not_legacy_markdown_scan():
    assert "run_walmart_cash_discovery(" in HELPER
    assert "run_walmart_scan(" not in HELPER
    assert "timeout=8" not in HELPER_C
    assert "timeout=15" not in HELPER_C


def test_cashfinder_zero_result_wording_is_truthful():
    assert "This is **not** a proven no-offer result" in OFFERS
    assert "Walmart API timed out before product data returned" in OFFERS
    assert "No Walmart API product rows returned" in OFFERS
    assert "No API-confirmed Cash Offers found in checked products" in OFFERS
