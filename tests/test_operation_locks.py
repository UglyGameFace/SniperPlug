import asyncio

from sniperplug.services.operation_locks import AsyncOperationLockRegistry, make_scan_operation_key


def test_operation_lock_acquire_blocks_duplicate_until_release():
    async def run():
        locks = AsyncOperationLockRegistry()
        key = make_scan_operation_key(
            guild_id=123,
            user_id=456,
            action="hunt_preset",
            parts=("glitch", 70),
        )

        first = await locks.acquire(key)
        duplicate = await locks.acquire(key)

        assert first is not None
        assert duplicate is None
        assert await locks.is_active(key) is True

        await locks.release(first)
        assert await locks.is_active(key) is False

        second = await locks.acquire(key)
        assert second is not None
        await locks.release(second)

    asyncio.run(run())


def test_operation_lock_key_includes_scan_dimensions():
    base = make_scan_operation_key(guild_id=1, user_id=2, action="rerun", parts=("lego", 1, 50))
    different_page = make_scan_operation_key(guild_id=1, user_id=2, action="rerun", parts=("lego", 2, 50))
    different_user = make_scan_operation_key(guild_id=1, user_id=3, action="rerun", parts=("lego", 1, 50))

    assert base != different_page
    assert base != different_user
