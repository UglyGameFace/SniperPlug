from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTO = (ROOT / "sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
MEMORY = (ROOT / "sniperplug/services/autoscan_observed_price_memory.py").read_text(encoding="utf-8")


def test_autoscan_uses_bounded_observed_price_memory_collector() -> None:
    assert "run_autoscan_verified_category_with_observed_memory" in AUTO
    assert "use_price_memory=False" not in AUTO
    assert "AUTOSCAN_PAGES_PER_QUERY = 2" in MEMORY
    assert "AUTOSCAN_SEARCH_CONCURRENCY = 3" in MEMORY
    assert "AUTOSCAN_OBSERVED_MEMORY_MAX_WRITES = 300" in MEMORY


def test_observed_memory_can_produce_verified_drop_cards() -> None:
    assert "select_observed_price_drop_cards" in MEMORY
    assert "use_price_memory=True" in MEMORY
    assert "cards = rank_verified_cards(memory_cards)" in MEMORY
