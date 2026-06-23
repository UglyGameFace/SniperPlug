import asyncio
import time

import pytest

from sniperplug.storage.db import _LibsqlAsyncConnection


class FakeResult:
    rows = []

    def fetchall(self):
        return []


class FakeLibsqlConnection:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def execute(self, sql, params=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls += 1
        if self.active > 1:
            raise RuntimeError("Stream already in use")
        time.sleep(0.03)
        self.active -= 1
        return FakeResult()

    def commit(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active > 1:
            raise RuntimeError("Stream already in use")
        time.sleep(0.03)
        self.active -= 1


@pytest.mark.asyncio
async def test_libsql_async_connection_serializes_concurrent_execute_calls():
    fake = FakeLibsqlConnection()
    conn = _LibsqlAsyncConnection(fake)

    await asyncio.gather(
        conn.execute("SELECT 1"),
        conn.execute("SELECT 2"),
        conn.execute("SELECT 3"),
    )

    assert fake.calls == 3
    assert fake.max_active == 1


@pytest.mark.asyncio
async def test_libsql_async_connection_serializes_execute_and_commit():
    fake = FakeLibsqlConnection()
    conn = _LibsqlAsyncConnection(fake)

    await asyncio.gather(
        conn.execute("SELECT 1"),
        conn.commit(),
    )

    assert fake.max_active == 1
