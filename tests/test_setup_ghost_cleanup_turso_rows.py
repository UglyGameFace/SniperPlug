from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sniperplug.services.setup_self_heal import (
    CONFIG_TABLES,
    _guild_id_from_row,
    cleanup_ghost_setup_rows,
    cleanup_ghost_setup_rows_detailed,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def fetchall(self):
        return list(self.rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, rows, *, ignore_first_delete_table: str | None = None):
        ids = {int(row[0]) for row in rows}
        self.rows_by_table = {table: set(ids) for table in CONFIG_TABLES}
        self.deleted: list[tuple[str, int]] = []
        self.commits = 0
        self.ignore_first_delete_table = ignore_first_delete_table
        self._ignored_once = False

    async def execute(self, sql: str, params=()):
        if sql.startswith("SELECT DISTINCT CAST(guild_id AS TEXT) AS guild_id FROM "):
            table = sql.split()[-1]
            return FakeCursor(
                [(str(guild_id),) for guild_id in sorted(self.rows_by_table[table])]
            )
        if sql.startswith("SELECT 1 AS present FROM "):
            table = sql.split()[5]
            guild_id = int(params[0])
            return FakeCursor([{"present": 1}] if guild_id in self.rows_by_table[table] else [])
        if sql.startswith("DELETE FROM "):
            table = sql.split()[2]
            guild_id = int(params[0])
            self.deleted.append((table, guild_id))
            if table == self.ignore_first_delete_table and not self._ignored_once:
                self._ignored_once = True
                return None
            self.rows_by_table[table].discard(guild_id)
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


def test_cleanup_deletes_true_tuple_row_ghost_from_every_config_table() -> None:
    live_id = 1514374173517152418
    true_ghost_id = 777777777777777777
    conn = FakeConnection([(live_id,), (true_ghost_id,)])
    db = FakeDatabase(conn)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=live_id)])

    deleted_count = asyncio.run(cleanup_ghost_setup_rows(db, bot))

    assert deleted_count == 1
    assert conn.commits == 1
    assert conn.deleted == [
        (table, true_ghost_id) for table in reversed(CONFIG_TABLES)
    ]
    assert all(true_ghost_id not in rows for rows in conn.rows_by_table.values())


def test_cleanup_retries_when_remote_delete_is_not_immediately_visible() -> None:
    live_id = 1514374173517152418
    true_ghost_id = 777777777777777777
    conn = FakeConnection(
        [(live_id,), (true_ghost_id,)],
        ignore_first_delete_table="guild_public_alert_settings",
    )
    db = FakeDatabase(conn)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=live_id)])

    result = asyncio.run(cleanup_ghost_setup_rows_detailed(db, bot))

    assert result == {"found": 1, "deleted": 1, "remaining": 0}
    assert conn.commits == 2
    assert conn.deleted.count(("guild_public_alert_settings", true_ghost_id)) == 2
    assert all(true_ghost_id not in rows for rows in conn.rows_by_table.values())
