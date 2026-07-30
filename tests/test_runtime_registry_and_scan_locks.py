from __future__ import annotations

import asyncio

import pytest

from sniperplug.providers.registry import ProviderRegistry
from sniperplug.services.scan_locks import ScanLockKey, ScanOperationLocks


class DummyProvider:
    def __init__(self, key: str) -> None:
        self.provider_key = key



def test_provider_registry_normalizes_keys_and_supports_controlled_replace() -> None:
    registry = ProviderRegistry()
    first = DummyProvider(" Walmart ")
    second = DummyProvider("walmart")

    registry.register(first)
    assert registry.get("WALMART") is first

    with pytest.raises(ValueError):
        registry.register(second)

    registry.register(second, replace=True)
    assert registry.get(" walmart ") is second

    registry.clear()
    assert registry.list_keys() == []


def test_scan_lock_blocks_whitespace_and_case_variants() -> None:
    async def scenario() -> None:
        locks = ScanOperationLocks(stale_seconds=60)
        first = ScanLockKey(guild_id=1, user_id=2, action="Deal Rerun", query="  OLED   TV  ", page=1, min_discount=50)
        duplicate = ScanLockKey(guild_id=1, user_id=2, action="deal rerun", query="oled tv", page=1, min_discount=50)

        assert await locks.acquire(first) is True
        assert await locks.acquire(duplicate) is False
        await locks.release(first)
        assert await locks.acquire(duplicate) is True

    asyncio.run(scenario())


def test_scan_lock_recovers_stale_entries() -> None:
    async def scenario() -> None:
        locks = ScanOperationLocks(stale_seconds=1)
        key = ScanLockKey(guild_id=1, user_id=2, action="hunt", query="lego")

        assert await locks.acquire(key) is True
        normalized = key.as_tuple()
        locks._active[normalized] -= 2
        assert await locks.acquire(key) is True

    asyncio.run(scenario())
