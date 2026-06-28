from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_autoscan_walmart_provider_uses_lightweight_scan_path() -> None:
    source = read("sniperplug/providers/cached_walmart.py")
    assert "def lightweight_autoscan_request" in source
    assert "requested_by" in source
    assert "autoscan" in source
    assert "_LIGHTWEIGHT_SCAN_NOTE" in source
    assert "db_persistence_skipped" in source
    assert "await self._scan_inner_direct(request)" in source


def test_observed_memory_writes_are_load_capped() -> None:
    source = read("sniperplug/services/walmart_observed_price_memory.py")
    assert "DEFAULT_OBSERVED_MEMORY_MAX_WRITES" in source
    assert "max_observations" in source
    assert "prioritized_observation_candidates" in source
    assert "skipped_due_to_load_cap" in source
    assert "load-capped" in source
