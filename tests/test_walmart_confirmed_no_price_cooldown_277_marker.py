from pathlib import Path


def test_bulk_runtime_marks_confirmed_no_price_cooldown_capability() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "sniperplug/services/walmart_exact_queue_bulk_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "confirmed_no_price_cooldown=api_price_missing_v277" in runtime
