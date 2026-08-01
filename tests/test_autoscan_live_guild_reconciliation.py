from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sniperplug.services import autoscan_live_guild_reconciliation as live
from sniperplug.services.setup_self_heal import SetupRepairResult


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.rowcount = 0

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, public_rows=()):
        self.public_rows = list(public_rows)
        self.commits = 0

    async def execute(self, sql: str, params=()):
        if "SELECT guild_id FROM guild_public_alert_settings" in sql:
            return _Cursor(self.public_rows)
        return _Cursor()

    async def commit(self):
        self.commits += 1


class _Database:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_live_loader_filters_stale_replica_rows_without_deleting(monkeypatch) -> None:
    real_id = 1514374173517152418
    ghost_id = 1514374173517152512
    conn = _Connection(public_rows=[(real_id,), (ghost_id,)])
    db = _Database(conn)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=real_id)])

    async def fake_tombstones(_conn):
        return {ghost_id}

    async def fake_config(_db, guild_id):
        assert guild_id == real_id
        return {"enabled": True, "retailers": ("walmart",), "channel_id": 987}

    monkeypatch.setattr(live, "load_ghost_tombstones", fake_tombstones)
    monkeypatch.setattr(live, "get_public_alert_config", fake_config)

    result = asyncio.run(live.list_live_public_alert_guilds(db, bot))

    assert [guild.guild_id for guild in result.guilds] == [real_id]
    assert result.stale_visible_ids == (ghost_id,)
    assert result.tombstoned_visible_ids == (ghost_id,)
    assert conn.commits == 0


def test_reconciliation_tombstones_ghost_once_then_ignores_stale_view(monkeypatch) -> None:
    real_id = 1514374173517152418
    ghost_id = 1514374173517152512
    conn = _Connection()
    db = _Database(conn)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=real_id)])
    tombstones: set[int] = set()
    delete_calls: list[set[int]] = []

    async def fake_clear(_conn, live_ids):
        tombstones.difference_update(set(live_ids))
        return 0

    async def fake_load(_conn):
        return set(tombstones)

    async def fake_discover(_conn, _live_ids):
        # Simulate a remote replica continuing to expose the old row.
        return {ghost_id}

    async def fake_delete(_conn, ids):
        delete_calls.append(set(ids))
        return []

    async def fake_remaining(_conn, _ids):
        return set()

    async def fake_mark(_conn, ids, *, reason):
        tombstones.update(set(ids))
        return len(set(ids))

    async def fake_repair(_db, _bot, guild_id):
        assert guild_id == real_id
        return SetupRepairResult(guild_id=guild_id, changed=False)

    monkeypatch.setattr(live, "clear_live_ghost_tombstones", fake_clear)
    monkeypatch.setattr(live, "load_ghost_tombstones", fake_load)
    monkeypatch.setattr(live, "_discover_ghost_ids", fake_discover)
    monkeypatch.setattr(live, "_delete_ghost_rows_once", fake_delete)
    monkeypatch.setattr(live, "_remaining_ghost_ids", fake_remaining)
    monkeypatch.setattr(live, "mark_ghost_tombstones", fake_mark)
    monkeypatch.setattr(live, "repair_public_alert_setup", fake_repair)

    first = asyncio.run(live.reconcile_live_public_alert_setups(db, bot))
    second = asyncio.run(live.reconcile_live_public_alert_setups(db, bot))

    assert first["ghost_rows_found"] == 1
    assert first["ghost_rows_quarantined"] == 1
    assert first["ghost_rows_already_quarantined"] == 0
    assert second["ghost_rows_found"] == 0
    assert second["ghost_rows_deleted"] == 0
    assert second["ghost_rows_already_quarantined"] == 1
    assert delete_calls == [{ghost_id}]


def test_is_live_bot_guild_fails_closed() -> None:
    bot = SimpleNamespace(get_guild=lambda guild_id: object() if guild_id == 123 else None)

    assert live.is_live_bot_guild(bot, 123) is True
    assert live.is_live_bot_guild(bot, 456) is False
    assert live.is_live_bot_guild(SimpleNamespace(), 123) is False
