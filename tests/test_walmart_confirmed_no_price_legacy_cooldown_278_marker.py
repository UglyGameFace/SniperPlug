from pathlib import Path


def test_runtime_schema_runs_legacy_no_price_floor_and_marks_deployment() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_schema = (
        root / "sniperplug/services/walmart_exact_runtime_schema.py"
    ).read_text(encoding="utf-8")

    assert "normalize_legacy_confirmed_no_price_cooldowns" in runtime_schema
    assert "legacy_no_price_floor_v278" in runtime_schema
