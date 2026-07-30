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
    assert "Manual broad sweep spans" in source
    assert "broad_public_safe" in source


def test_native_autoscan_uses_verified_only_public_threshold() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "Verified-only public policy" in source
    assert "Anything uncertain remains private for staff review" in source
    assert "min_public_discount=result.min_discount" in source
    assert "NATIVE_SCOUT_MIN_SCORE" not in source
    assert "allow_review_scout=True" not in source
