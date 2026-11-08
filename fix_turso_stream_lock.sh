#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "🔧 Fixing Turso/libsql stream concurrency"
cd ~/SniperPlug

python - <<'PY'
from pathlib import Path
import re

p = Path("sniperplug/storage/db.py")
s = p.read_text(encoding="utf-8")

old = '''class _LibsqlAsyncConnection:
    def __init__(self, conn: Any):
        self.conn = conn

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> _LibsqlAsyncCursor:
        def run() -> _LibsqlAsyncCursor:
            if params is None:
                result = self.conn.execute(sql)
            else:
                result = self.conn.execute(sql, tuple(params))
            return _LibsqlAsyncCursor(result)

        return await asyncio.to_thread(run)

    async def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            await self.execute(statement)

    async def commit(self) -> None:
        commit = getattr(self.conn, "commit", None)
        if callable(commit):
            await asyncio.to_thread(commit)

    async def close(self) -> None:
        close = getattr(self.conn, "close", None)
        if callable(close):
            await asyncio.to_thread(close)
'''

new = '''class _LibsqlAsyncConnection:
    def __init__(self, conn: Any):
        self.conn = conn
        self._lock = asyncio.Lock()

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> _LibsqlAsyncCursor:
        def run() -> _LibsqlAsyncCursor:
            if params is None:
                result = self.conn.execute(sql)
            else:
                result = self.conn.execute(sql, tuple(params))
            return _LibsqlAsyncCursor(result)

        async with self._lock:
            return await asyncio.to_thread(run)

    async def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            await self.execute(statement)

    async def commit(self) -> None:
        commit = getattr(self.conn, "commit", None)
        if callable(commit):
            async with self._lock:
                await asyncio.to_thread(commit)

    async def close(self) -> None:
        close = getattr(self.conn, "close", None)
        if callable(close):
            async with self._lock:
                await asyncio.to_thread(close)
'''

if old not in s:
    raise SystemExit("Could not find the old _LibsqlAsyncConnection block. Stop and inspect sniperplug/storage/db.py")

s = s.replace(old, new)
p.write_text(s, encoding="utf-8")

test = Path("tests/test_libsql_connection_lock.py")
test.write_text('''import asyncio
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
''', encoding="utf-8")

print("✅ Added native libsql async lock")
print("✅ Added regression test for Hrana stream concurrency")
PY

echo "🧪 Compile check..."
python -m compileall -q sniperplug

echo "🧪 Focused tests..."
python -m pytest -q \
  tests/test_libsql_connection_lock.py \
  tests/test_static_regressions.py \
  tests/test_verizon_shine_timezone.py \
  tests/test_walmart_provider.py \
  tests/test_walmart_visible_savings_reference_guard.py

echo "📋 Git status:"
git status --short

git add sniperplug/storage/db.py tests/test_libsql_connection_lock.py

# Also pick up your previous embed cleanup if it is still sitting uncommitted.
git add -A

git commit -m "Serialize Turso libsql connection operations"
git push origin main

echo "✅ Done. Redeploy SniperPlug."
