from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_native_autoscan_uses_catalog_wide_rotation() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "rotating_catalog_slice" in source
    assert "CatalogCoverageSlice" in source
    assert "select_native_autoscan_preset" in source
    assert "legacy.select_autoscan_preset" not in source
    assert 'NATIVE_BROAD_PRESET_KEY = "catalog_wide_rotating"' in source


def test_native_autoscan_reports_truthful_bounded_catalog_coverage() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Catalog-Wide Rotating Sweep" in source
    assert "rotating catalog route(s)" in source
    assert "coverage.summary_line()" in source
    assert "global exact-detail queue" in read("sniperplug/services/walmart_catalog_coverage.py")


def test_native_manual_autoscan_includes_walmart_cash_discovery() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    coverage = read("sniperplug/services/walmart_catalog_coverage.py")
    assert "Walmart Cash routes are included for discovery" in source
    assert "DEFAULT_CASH_QUERIES" in coverage
    assert "Walmart Cash" in source
    assert "Cash never substitutes for trusted markdown proof" in source


def test_native_autoscan_uses_verified_only_public_threshold() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Verified-only result policy" in source
    assert "Anything uncertain is suppressed and never shown as a deal" in source
    assert "min_public_discount=result.min_discount" in source
    assert 'public_mode="Exact-Verified Deals Only"' in source
    assert "NATIVE_SCOUT_MIN_SCORE" not in source
    assert "allow_review_scout=True" not in source


def test_scheduled_and_manual_native_autoscan_remain_bounded() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT" in source
    assert "NATIVE_MANUAL_QUERY_COUNT = 8" in source
    assert "legacy.AUTO_SCAN_FAST_QUERY_COUNT" not in source
    assert "resolve_native_query_count" in source
    assert "rotating_catalog_slice(guild_id=guild_id, query_count=query_count)" in source


def test_exact_cards_are_refreshed_between_repeated_public_gates() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert source.count("normalize_exact_verified_walmart_cards(") >= 3
    proof_index = source.index("proof_ready_cards = legacy.select_public_deal_candidates")
    freshness_refresh = source.index(
        "normalize_exact_verified_walmart_cards(\n            public_candidates",
        proof_index,
    )
    fresh_index = source.index("fresh_selection = await legacy.select_fresh_deal_cards", freshness_refresh)
    final_refresh = source.index(
        "normalize_exact_verified_walmart_cards(\n            shown_cards",
        fresh_index,
    )
    final_post = source.index("public_result = await legacy.maybe_post_public_deal_cards", final_refresh)
    assert proof_index < freshness_refresh < fresh_index < final_refresh < final_post


def test_only_global_autoscan_runner_is_imported_by_runtime() -> None:
    source = read("sniperplug/bot.py")
    global_source = read("sniperplug/cogs/global_auto_scan_runner.py")
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\n" not in source
    assert "from sniperplug.cogs.global_auto_scan_runner import AutoScanRunnerCog as GlobalAutoScanRunnerCog" in source
    assert source.count("await self.add_cog(GlobalAutoScanRunnerCog(self))") == 1
    assert "class AutoScanRunnerCog(resilient.AutoScanRunnerCog)" in global_source
