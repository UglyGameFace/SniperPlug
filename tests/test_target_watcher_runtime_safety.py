from __future__ import annotations

import asyncio

import pytest

from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.config import TargetWatcherSettings


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
