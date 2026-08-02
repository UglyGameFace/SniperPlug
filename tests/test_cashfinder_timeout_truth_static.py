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
        if line.startswith(("    @app_commands.", "    @commands.", "    async def ", "    def ")):
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


def test_cashfinder_unsupported_and_timeout_copy_is_truthful_and_compact():
    assert "Walmart Cash feed unavailable" in OFFERS
    assert "Fake no-offer conclusion" in OFFERS
    assert "Product searches made" in OFFERS
    assert "Item-detail calls made" in OFFERS
    assert "Partial check" in OFFERS
    assert "fake zero" in OFFERS.lower()
    assert "public PDP scraping" in OFFERS
