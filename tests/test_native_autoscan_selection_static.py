from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_native_autoscan_uses_public_safe_presets() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "public_autoscan_hunt_presets" in source
    assert "select_native_autoscan_preset" in source
    assert "legacy.select_autoscan_preset" not in source
    assert "PUBLIC_AUTOSCAN_ROUTE_POLICY_NOTE" in source


def test_native_autoscan_category_rotation_is_explicit() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_CATEGORY_ROTATION" in source
    assert '"open_box"' in source
    assert '"deal_week"' in source
    assert '"tech"' in source
    assert '"auto_tools"' in source


def test_native_manual_autoscan_is_broad_public_safe_sweep() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_BROAD_PRESET_KEY" in source
    assert "build_native_broad_preset" in source
    assert "Broad Public-Safe Sweep" in source
    assert "Broad sweep spans" in source
    assert "broad_public_safe" in source


def test_native_autoscan_uses_verified_only_public_threshold() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Verified-only result policy" in source
    assert "Anything uncertain is suppressed and never shown as a deal" in source
    assert "min_public_discount=result.min_discount" in source
    assert 'public_mode="Exact-Verified Deals Only"' in source
    assert "NATIVE_SCOUT_MIN_SCORE" not in source
    assert "allow_review_scout=True" not in source


def test_scheduled_native_autoscan_spreads_bounded_routes_across_categories() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT" in source
    assert "legacy.AUTO_SCAN_FAST_QUERY_COUNT" not in source
    assert source.count("return build_native_broad_preset(presets, guild_id=guild_id, query_count=query_count)") == 1
    assert "bucket = int(time.time()" not in source


def test_only_resilient_autoscan_runner_is_imported_by_runtime() -> None:
    source = read("sniperplug/bot.py")
    assert "from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog\n" not in source
    assert "from sniperplug.cogs.resilient_auto_scan_runner import AutoScanRunnerCog as ResilientAutoScanRunnerCog" in source
    assert source.count("await self.add_cog(ResilientAutoScanRunnerCog(self))") == 1
