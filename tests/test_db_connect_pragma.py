"""Regression: Database.connect must not leave the WAL pragma statement open.

``PRAGMA journal_mode=WAL`` returns a row. If its cursor is not consumed,
the statement stays active on the connection, and the first real COMMIT
afterwards fails with "cannot commit transaction - SQL statements in
progress" whenever the cursor object has not been garbage-collected yet.
That GC race is what made the consolidation announcement test fail
intermittently in CI (root-caused 2026-11-23): connect() discarded the
pragma cursor, and the public-alert migration's marker insert was the
first transaction to commit.

This test simulates the losing side of the race deterministically by
retaining every cursor handed out during connect, so finalization cannot
rescue the old code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sniperplug.storage import db as db_module
from sniperplug.storage.db import Database


@pytest.mark.asyncio
async def test_connect_leaves_no_statement_open_for_first_commit(
    tmp_path: Path, monkeypatch
) -> None:
    retained: list = []
    real_execute = db_module.aiosqlite.Connection.execute

    async def retaining_execute(self, sql, parameters=None):
        cursor = await real_execute(self, sql, parameters)
        retained.append(cursor)
        return cursor

    monkeypatch.setattr(
        db_module.aiosqlite.Connection, "execute", retaining_execute
    )
    db = Database(str(tmp_path / "pragma.sqlite3"))
    await db.connect()
    try:
        conn = db.require_conn()
        await conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
        await conn.execute("INSERT INTO t VALUES ('x')")
        # The first transactional commit after connect: this raised
        # "cannot commit transaction - SQL statements in progress" on the
        # pre-fix connect() whenever the pragma cursor was still alive.
        await conn.commit()
    finally:
        await db.close()
