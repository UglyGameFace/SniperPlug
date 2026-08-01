from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sniperplug.services.setup_self_heal import (
    CONFIG_TABLES,
    _guild_id_from_row,
    cleanup_ghost_setup_rows,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.deleted: list[tuple[str, int]] = []
        self.commits = 0

    async def execute(self, sql: str, params=()):
        if sql.startswith("SELECT DISTINCT guild_id FROM "):
            return FakeCursor(self.rows)
        if sql.startswith("DELETE FROM "):
            table = sql.split()[2]
            self.deleted.append((table, int(params[0])))
            return None
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        self.commits += 1


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_guild_id_reader_supports_mapping_tuple_and_attribute_rows() -> None:
    assert _guild_id_from_row({"guild_id": "123"}) == 123
    assert _guild_id_from_row((456,)) == 456
    assert _guild_id_from_row(SimpleNamespace(guild_id="789")) == 789
    assert _guild_id_from_row(("not-an-id",)) is None


def test_cleanup_deletes_tuple_row_ghost_from_every_config_table() -> None:
    live_id = 1514374173517152418
    ghost_id = 1514374173517152512
    conn = FakeConnection([(live_id,), (ghost_id,)])
    db = FakeDatabase(conn)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=live_id)])

    deleted_count = asyncio.run(cleanup_ghost_setup_rows(db, bot))

    assert deleted_count == 1
    assert conn.commits == 1
    assert conn.deleted == [(table, ghost_id) for table in CONFIG_TABLES]
