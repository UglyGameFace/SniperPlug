from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services import active_deal_history
from sniperplug.services import walmart_recheck_audit


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _noop_ensure(_db) -> None:
    return None


def test_global_active_history_prune_deletes_only_bounded_old_batch(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await conn.execute(
            """
            CREATE TABLE guild_active_deal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_history_time ON guild_active_deal_history (occurred_at, id)"
        )
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        await conn.executemany(
            "INSERT INTO guild_active_deal_history (guild_id, occurred_at) VALUES (?, ?)",
            [(1, old) for _ in range(120)] + [(1, recent) for _ in range(5)],
        )
        await conn.commit()
        monkeypatch.setattr(active_deal_history, "ensure_active_deal_history", _noop_ensure)

        deleted = await active_deal_history.prune_active_deal_history(db, batch_size=25)
        assert deleted == 25
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_active_deal_history")
        assert (await cursor.fetchone())["count"] == 100
        await conn.close()

    asyncio.run(run())


def test_per_guild_active_history_cap_is_bounded(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await conn.execute(
            """
            CREATE TABLE guild_active_deal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_history_guild_time ON guild_active_deal_history (guild_id, occurred_at DESC, id DESC)"
        )
        now = datetime.now(timezone.utc)
        await conn.executemany(
            "INSERT INTO guild_active_deal_history (guild_id, occurred_at) VALUES (?, ?)",
            [(7, (now - timedelta(minutes=index)).isoformat()) for index in range(80)],
        )
        await conn.commit()
        monkeypatch.setattr(active_deal_history, "ensure_active_deal_history", _noop_ensure)

        deleted = await active_deal_history.prune_active_deal_history(
            db,
            guild_id=7,
            retention_days=365,
            max_rows_per_guild=50,
            batch_size=10,
        )
        assert deleted == 10
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_active_deal_history")
        assert (await cursor.fetchone())["count"] == 70
        await conn.close()

    asyncio.run(run())


def test_global_walmart_recheck_audit_prune_deletes_only_bounded_old_batch(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await conn.execute(
            """
            CREATE TABLE guild_walmart_recheck_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX idx_audit_time ON guild_walmart_recheck_audit (occurred_at, id)"
        )
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        await conn.executemany(
            "INSERT INTO guild_walmart_recheck_audit (guild_id, occurred_at) VALUES (?, ?)",
            [(1, old) for _ in range(90)] + [(2, recent) for _ in range(4)],
        )
        await conn.commit()
        monkeypatch.setattr(walmart_recheck_audit, "ensure_walmart_recheck_audit", _noop_ensure)

        deleted = await walmart_recheck_audit.prune_walmart_recheck_audit(db, batch_size=20)
        assert deleted == 20
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_walmart_recheck_audit")
        assert (await cursor.fetchone())["count"] == 74
        await conn.close()

    asyncio.run(run())
