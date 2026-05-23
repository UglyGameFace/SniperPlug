import asyncio

from sniperplug.services.scan_locks import ScanLockKey, ScanOperationLocks


def test_scan_lock_acquire_release_cycle():
    async def run_check():
        locks = ScanOperationLocks()
        key = ScanLockKey(guild_id=1, user_id=2, action="hunt", preset="glitch", page=1, min_discount=70)
        first = await locks.acquire(key)
        second = await locks.acquire(key)
        await locks.release(key)
        third = await locks.acquire(key)
        assert first is True
        assert second is False
        assert third is True

    asyncio.run(run_check())


def test_scan_lock_normalizes_key_parts():
    async def run_check():
        locks = ScanOperationLocks()
        key_a = ScanLockKey(guild_id=1, user_id=2, action=" Hunt ", preset=" Glitch ", query=" OLED TV ")
        key_b = ScanLockKey(guild_id=1, user_id=2, action="hunt", preset="glitch", query="oled tv")
        first = await locks.acquire(key_a)
        second = await locks.acquire(key_b)
        assert first is True
        assert second is False

    asyncio.run(run_check())
