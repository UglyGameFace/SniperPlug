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


def test_cash_truth_blocks_non_walmart_cash_promos():
    lowered = CASH_TRUTH.lower()
    for token in ("onepay", "credit card", "cashback", "cash rewards", "buy more", "save up to", "view eligible items"):
        assert token in lowered


def test_cash_truth_requires_real_walmart_cash_amount():
    lower = CASH_TRUTH.lower()
    assert "walmartcashsavings" in lower
    assert "walmartcashamount" in lower
    assert "strict_api_field_amount" in CASH_TRUTH
    assert "amount <= 0" in CASH_TRUTH or "amount is None" in CASH_TRUTH


def test_walmart_cash_command_uses_scoped_fast_helper():
    assert "ProviderScanRequest(" in HELPER
    assert "provider.scan(" in HELPER
    assert "queries[:2]" in HELPER
    assert "timeout=8" not in HELPER_C
