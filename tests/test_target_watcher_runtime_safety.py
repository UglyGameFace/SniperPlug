from __future__ import annotations

import asyncio

import pytest

from sniperplug.services.target_locations import TargetLocationContext
from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.config import TargetWatcherSettings


LOCATION = TargetLocationContext(
    scope_type="guild",
    scope_id="1",
    zip_code="06604",
    store_id="1956",
    store_name="Target Trumbull",
    address_line="120 Hawley Ln",
    city="Trumbull",
    state="CT",
    postal_code="06611",
    latitude="41.23",
    longitude="-73.15",
)


def test_target_redsky_key_is_required_as_a_secret() -> None:
    missing = TargetWatcherSettings(
        redsky_api_key="",
        require_remote_database=False,
    )
    with pytest.raises(RuntimeError, match="deployment secret"):
        missing.validate_runtime()

    configured = TargetWatcherSettings(
        redsky_api_key="test-runtime-secret",
        require_remote_database=False,
    )
    configured.validate_runtime()


def test_target_settings_have_no_process_wide_location_fallback() -> None:
    settings = TargetWatcherSettings(
        redsky_api_key="test-runtime-secret",
        require_remote_database=False,
    )
    for forbidden in ("store_id", "zip_code", "state", "latitude", "longitude"):
        assert not hasattr(settings, forbidden)


def test_target_client_requires_and_uses_explicit_location_context() -> None:
    settings = TargetWatcherSettings(
        redsky_api_key="test-runtime-secret",
        require_remote_database=False,
    )
    client = TargetRedSkyClient(settings)
    assert client._geo_params(LOCATION) == {
        "store_id": "1956",
        "pricing_store_id": "1956",
        "zip": "06604",
        "state": "CT",
        "latitude": "41.23",
        "longitude": "-73.15",
    }
    with pytest.raises(ValueError, match="explicit saved location"):
        client._geo_params(None)  # type: ignore[arg-type]


def test_target_batches_are_capped_to_redsky_fulfillment_limit(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_PRODUCT_BATCH_SIZE", "100")
    monkeypatch.setenv("TARGET_PRODUCTS_PER_LOCATION_BATCH", "100")
    settings = TargetWatcherSettings.from_env()
    assert settings.product_batch_size == 24
    assert settings.products_per_location_batch == 24


def test_target_sitemap_session_preserves_compressed_wire_bytes() -> None:
    async def run() -> None:
        settings = TargetWatcherSettings(
            redsky_api_key="test-runtime-secret",
            require_remote_database=False,
        )
        async with TargetRedSkyClient(settings) as client:
            assert client._session is not None
            assert client._sitemap_session is not None
            assert client._session._auto_decompress is True
            assert client._sitemap_session._auto_decompress is False

    asyncio.run(run())
