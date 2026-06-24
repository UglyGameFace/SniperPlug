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


def test_cashfinder_timeout_is_not_shorter_than_provider_timeout():
    assert "provider_timeout" in HELPER
    assert "timeout_seconds" in HELPER
    assert "cash_route_timeout" in HELPER
    assert "provider_timeout + 4" in HELPER
    assert "timeout=8" not in HELPER_C
    assert "timeout=15" not in HELPER_C


def test_cashfinder_uses_direct_provider_scan_for_cash_rows():
    assert "provider.scan(" in HELPER
    assert "ProviderScanRequest(" in HELPER
    assert "mode" in HELPER and "walmart_cash" in HELPER
    assert "run_walmart_scan(" not in HELPER


def test_cashfinder_zero_result_wording_is_truthful():
    assert "This is **not** a proven no-offer result" in OFFERS
    assert "Walmart API timed out before product data returned" in OFFERS
    assert "No Walmart API product rows returned" in OFFERS
    assert "No API-confirmed Cash Offers found in checked products" in OFFERS
