from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.services import walmart_global_catalog_autoscan as catalog


class CountingConnection:
    """Turso-like wrapper that tracks SQL statements without mocking SQLite."""
    def __init__(self, connection):
        self.connection = connection
        self.commands = []
        self.commits = 0
        self.fail_insert_once = False

    async def execute(self, sql, params=()):
        statement = " ".join(str(sql).split())
        self.commands.append(statement)
        if self.fail_insert_once and statement.startswith("INSERT INTO walmart_global_catalog_autoscan_state"):
            self.fail_insert_once = False
            raise RuntimeError("transient database write error")
        return await self.connection.execute(sql, params)

    async def commit(self):
        self.commits += 1
        return await self.connection.commit()


def test_catalog_initialization_is_one_time_per_connection_even_with_concurrent_calls() -> None:
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        wrapper = CountingConnection(conn)
        db = SimpleNamespace(require_conn=lambda: wrapper)
        try:
            await asyncio.gather(
                *(catalog.ensure_global_catalog_state(db) for _ in range(6))
            )
            assert sum("CREATE TABLE IF NOT EXISTS walmart_global_catalog_autoscan_state" in command
                       for command in wrapper.commands) == 1
            assert sum(command.startswith("INSERT INTO walmart_global_catalog_autoscan_state")
                       for command in wrapper.commands) == 1
            assert wrapper.commits == 1

            for _ in range(8):
                await catalog.load_global_catalog_state(db)
            assert wrapper.commits == 1
            assert sum(command.startswith("INSERT INTO walmart_global_catalog_autoscan_state")
                       for command in wrapper.commands) == 1

            # Routine claim/completion each updates one durable cursor, without
            # rerunning DDL or the conflict-ignored initialization insert.
            started = datetime(2026, 11, 2, tzinfo=timezone.utc)
            claim = await catalog.claim_next_catalog_routes(db, route_count=3, now=started)
            assert claim is not None
            assert await catalog.complete_catalog_claim(db, claim, now=started)
            assert wrapper.commits == 3
            assert sum(command.startswith("INSERT INTO walmart_global_catalog_autoscan_state")
                       for command in wrapper.commands) == 1

            # A new database connection is initialized independently.
            newer = await aiosqlite.connect(":memory:")
            wrapped_newer = CountingConnection(newer)
            await catalog.ensure_global_catalog_state(
                SimpleNamespace(require_conn=lambda: wrapped_newer)
            )
            assert wrapped_newer.commits == 1
            await newer.close()
        finally:
            await conn.close()
    asyncio.run(scenario())


def test_catalog_schema_failure_retries_without_marking_connection_ready() -> None:
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        wrapper = CountingConnection(conn)
        wrapper.fail_insert_once = True
        db = SimpleNamespace(require_conn=lambda: wrapper)
        try:
            with pytest.raises(RuntimeError, match="transient database"):
                await catalog.ensure_global_catalog_state(db)
            assert wrapper.commits == 0
            await catalog.ensure_global_catalog_state(db)
            assert wrapper.commits == 1
            state = await catalog.load_global_catalog_state(db)
            assert state.cursor_index == 0
            assert wrapper.commits == 1
        finally:
            await conn.close()
    asyncio.run(scenario())
