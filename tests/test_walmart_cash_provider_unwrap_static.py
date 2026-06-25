from pathlib import Path


PIPELINE = Path("sniperplug/services/walmart_cash_pipeline.py").read_text(encoding="utf-8")
CACHED = Path("sniperplug/providers/cached_walmart.py").read_text(encoding="utf-8")


def test_cash_finder_unwraps_cached_walmart_provider_for_api_truth():
    assert "_unwrap_walmart_provider" in PIPELINE
    assert "api_provider = _unwrap_walmart_provider(provider)" in PIPELINE
    assert "api_provider.scan" in PIPELINE
    assert '"skip_scan_cache": "yes"' in PIPELINE


def test_cached_walmart_exposes_detail_and_honors_skip_cache():
    assert "def config" in CACHED
    assert "fetch_product_detail_payload" in CACHED
    assert "return await self.inner.fetch_product_detail_payload(item_id)" in CACHED
    assert "skip_scan_cache" in CACHED
    assert "return await self.inner.scan(request)" in CACHED
