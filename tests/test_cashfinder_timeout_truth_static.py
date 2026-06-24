from pathlib import Path
import re

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def method_source(src: str, name: str) -> str:
    marker = f"async def {name}("
    start = src.index(marker)
    search_from = start + len(marker)
    candidates = []
    for token in ("\\n    @app_commands.", "\\n    async def ", "\\n    def "):
        pos = src.find(token, search_from)
        if pos != -1:
            candidates.append(pos)
    end = min(candidates) if candidates else len(src)
    return src[start:end]


def compact(text: str) -> str:
    return re.sub(r"\\s+", "", text)


HELPER = method_source(DEALS, "_send_walmart_cash_search")
HELPER_C = compact(HELPER)


def test_cashfinder_timeout_is_not_shorter_than_provider_timeout():
    assert "provider_timeout=int(getattr(getattr(provider,\"config\",None),\"timeout_seconds\",12)or12)" in HELPER_C
    assert "cash_route_timeout=max(provider_timeout+4,16)" in HELPER_C
    assert "timeout=8" not in HELPER
    assert "timeout=15" not in HELPER


def test_cashfinder_uses_direct_provider_scan_for_cash_rows():
    assert "provider.scan(" in HELPER
    assert "ProviderScanRequest(" in HELPER
    assert "mode\": \"walmart_cash\"" in HELPER
    assert "run_walmart_scan(query, page, per_route_limit" not in HELPER


def test_cashfinder_summary_shows_only_used_routes():
    assert "used_queries = tuple(query for query, _page in scan_jobs)" in HELPER
    assert "build_walmart_cash_summary_embed(search, used_queries" in HELPER
    assert "build_walmart_cash_summary_embed(search, queries" not in HELPER


def test_cashfinder_zero_result_wording_is_truthful():
    assert "This is **not** a proven no-offer result" in OFFERS
    assert "Walmart API timed out before product data returned" in OFFERS
    assert "No Walmart API product rows returned" in OFFERS
    assert "No API-confirmed Cash Offers found in checked products" in OFFERS
