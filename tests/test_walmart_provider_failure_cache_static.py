from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_cached_walmart_health_validates_private_key_before_ready() -> None:
    source = read("sniperplug/providers/cached_walmart.py")

    assert "def walmart_credential_validation_error" in source
    assert "_load_private_key" in source
    assert "Walmart credentials are present but unusable" in source
    assert "ProviderStatus.ERROR" in source


def test_cached_walmart_does_not_cache_hard_provider_failures() -> None:
    source = read("sniperplug/providers/cached_walmart.py")

    assert "provider_scan_had_hard_failure" in source
    assert "ignored cached Walmart provider failure result" in source
    assert "status=\"provider_error\" if provider_scan_had_hard_failure(result) else \"finished\"" in source
    assert "if provider_scan_had_hard_failure(result):" in source
    assert "await self.db.set_scan_result_cache" in source


def test_deep_scan_cache_does_not_reuse_or_write_provider_failures() -> None:
    source = read("sniperplug/services/scan_result_accelerator.py")

    assert "Provider/auth failures are never trusted as cache hits" in source
    assert "if not provider_scan_had_hard_failure(result):" in source
    assert "not provider_scan_had_hard_failure(result)" in source
    assert "provider_hard_failure" in source
    assert "Provider hard failure" in source
