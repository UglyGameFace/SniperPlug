from pathlib import Path
import re

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")
PIPELINE = Path("sniperplug/services/walmart_cash_pipeline.py").read_text(encoding="utf-8")
CLASSIFIER = Path("sniperplug/services/walmart_promo_classifier.py").read_text(encoding="utf-8")
OFFERS = Path("sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")
PROVIDER = Path("sniperplug/providers/walmart.py").read_text(encoding="utf-8")


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


def test_cash_command_delegates_to_scoped_pipeline():
    assert "run_walmart_cash_discovery" in HELPER
    assert "build_walmart_cash_summary_embed" in HELPER
    assert "HuntPresetMenuView" not in HELPER
    assert "timeout=150" not in re.sub(r"\s+", "", HELPER)


def test_pipeline_is_bounded_official_api_only_and_has_detail_stage():
    assert "queries[:DEFAULT_ROUTE_LIMIT]" in PIPELINE
    assert "DEFAULT_ROUTE_LIMIT = 6" in PIPELINE
    assert "DETAIL_CANDIDATE_LIMIT = 24" in PIPELINE
    assert "SEARCH_CONCURRENCY = 3" in PIPELINE
    assert "DETAIL_CONCURRENCY = 4" in PIPELINE
    assert "fetch_product_detail_payload" in PIPELINE
    assert "detail_rows_checked" in PIPELINE
    assert '"public_pdp_fallback": "disabled"' in PIPELINE
    assert "walmart_pdp_cash_proof" not in PIPELINE
    assert "check_walmart_pdp_cash_truth" not in PIPELINE


def test_classifier_separates_promo_types():
    lower = CLASSIFIER.lower()
    for token in ("onepay", "buy more", "save up to", "rollback", "clearance", "generic_promo"):
        assert token in lower


def test_owner_probe_and_compact_embed_are_wired():
    assert 'name="walmart_api_probe"' in DEALS
    assert "run_walmart_api_probe" in DEALS
    assert "build_walmart_api_probe_embed" in OFFERS
    assert "public PDP scraping disabled" in OFFERS


def test_provider_has_official_detail_fetch_without_removing_normal_scan():
    assert "fetch_product_detail_payload" in PROVIDER
    assert "detail_url" in PROVIDER
    assert "def _search(" in PROVIDER
    assert "def scan(" in PROVIDER
