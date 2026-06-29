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


def test_native_autoscan_thresholds_are_split() -> None:
    source = read("sniperplug/cogs/native_auto_scan_runner.py")
    assert "NATIVE_SCOUT_MIN_SCORE = 95" in source
    assert "verified markdown requires" in source
    assert "Public Scout fallback requires" in source
    assert "min_alert_score=NATIVE_SCOUT_MIN_SCORE" in source
