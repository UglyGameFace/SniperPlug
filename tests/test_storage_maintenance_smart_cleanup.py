from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services import storage_maintenance as maintenance


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class MappingCursor:
    """Normalize SQLite rows to the mapping shape returned by the Turso worker."""

    def __init__(self, inner):
        self.inner = inner

    async def fetchall(self):
        rows = await self.inner.fetchall()
        columns = [str(item[0]) for item in (self.inner.description or ())]
        return [
            {columns[index]: row[index] for index in range(len(columns))}
            for row in rows
        ]

    async def fetchone(self):
        rows = await self.fetchall()
        return rows[0] if rows else None

    @property
    def rowcount(self):
        return self.inner.rowcount


class MappingConnection:
    def __init__(self, inner):
        self.inner = inner
        self.executed_sql: list[str] = []

    async def execute(self, sql, params=()):
        self.executed_sql.append(" ".join(str(sql).split()))
        cursor = await self.inner.execute(sql, params)
        return MappingCursor(cursor)

    async def commit(self):
        await self.inner.commit()


class EmptyCursor:
    async def fetchall(self):
        return []

    async def fetchone(self):
        return None


class EmptyMaintenanceConnection:
    def __init__(self):
        self.commits = 0
        self.executed_sql: list[str] = []

    async def execute(self, sql, params=()):
        self.executed_sql.append(" ".join(str(sql).split()))
        return EmptyCursor()

    async def commit(self):
        self.commits += 1


async def _noop_ensure(_db) -> None:
    return None


async def _noop_prune(_db) -> int:
    return 0


async def _no_optional_table(_conn, _table: str) -> bool:
    return False


async def _noop_indexes(_conn) -> None:
    return None


def _install_noop_ensures(monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "ensure_deal_feedback_tables", _noop_ensure)
    monkeypatch.setattr(maintenance, "ensure_public_post_tables", _noop_ensure)
    monkeypatch.setattr(maintenance, "ensure_autoscan_history_table", _noop_ensure)
    monkeypatch.setattr(maintenance, "ensure_error_logging_table", _noop_ensure)
    monkeypatch.setattr(maintenance, "prune_active_deal_history", _noop_prune)
    monkeypatch.setattr(maintenance, "prune_walmart_recheck_audit", _noop_prune)


def test_smart_cleanup_drops_disposable_rows_but_keeps_price_history(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        now = datetime.now(timezone.utc)
        old_40d = (now - timedelta(days=40)).isoformat()
        old_10d = (now - timedelta(days=10)).isoformat()
        old_100d = (now - timedelta(days=100)).isoformat()
        recent = (now - timedelta(hours=1)).isoformat()

        await conn.executescript(
            """
            CREATE TABLE guild_deal_feedback_targets (created_at TEXT, expires_at TEXT);
            CREATE TABLE guild_deal_feedback_events (created_at TEXT);
            CREATE TABLE guild_active_deal_cache (
                guild_id INTEGER,
                active_key TEXT,
                status TEXT,
                last_seen_at TEXT
            );
            CREATE TABLE guild_auto_scan_report_history (ran_at TEXT);
            CREATE TABLE guild_public_deal_posts (
                status TEXT,
                first_seen_at TEXT,
                posted_at TEXT
            );
            CREATE TABLE provider_response_cache (expires_at TEXT);
            CREATE TABLE scan_result_cache (expires_at TEXT);
            CREATE TABLE alert_dedupe (expires_at TEXT);
            CREATE TABLE store_cache (expires_at TEXT);
            CREATE TABLE scan_runs (started_at TEXT);
            CREATE TABLE error_events (created_at TEXT);
            CREATE TABLE guild_retailer_auto_scan_runs (ran_at TEXT);
            CREATE TABLE user_dm_deal_alert_receipts (delivered_at TEXT);
            CREATE TABLE price_observations (
                observation_id TEXT PRIMARY KEY,
                retailer TEXT,
                product_key TEXT,
                current_price REAL,
                observed_at TEXT
            );
            """
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'old-active', 'active', ?)",
            (old_10d,),
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'old-stale', 'stale', ?)",
            (old_10d,),
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'old-cleared', 'cleared', ?)",
            (old_10d,),
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'recent', 'active', ?)",
            (recent,),
        )
        await conn.execute(
            "INSERT INTO price_observations VALUES ('history-1', 'walmart', 'item-1', 19.99, ?)",
            (old_100d,),
        )
        for table in (
            "provider_response_cache",
            "scan_result_cache",
            "alert_dedupe",
            "store_cache",
        ):
            await conn.execute(f"INSERT INTO {table} VALUES (?)", (old_10d,))
        await conn.execute("INSERT INTO scan_runs VALUES (?)", (old_10d,))
        await conn.execute("INSERT INTO error_events VALUES (?)", (old_40d,))
        await conn.execute("INSERT INTO guild_auto_scan_report_history VALUES (?)", (old_10d,))
        await conn.execute(
            "INSERT INTO guild_public_deal_posts VALUES ('posted', ?, ?)",
            (old_40d, old_40d),
        )
        await conn.execute(
            "INSERT INTO guild_public_deal_posts VALUES ('sending', ?, NULL)",
            (old_40d,),
        )
        await conn.execute("INSERT INTO guild_retailer_auto_scan_runs VALUES (?)", (old_10d,))
        await conn.execute("INSERT INTO user_dm_deal_alert_receipts VALUES (?)", (old_100d,))
        await conn.commit()

        _install_noop_ensures(monkeypatch)
        maintenance._CLEANUP_INDEX_CONNECTIONS.clear()

        result = await maintenance.run_storage_maintenance(
            db,
            cleanup_batch_size=50,
        )

        cursor = await conn.execute(
            "SELECT active_key, status FROM guild_active_deal_cache ORDER BY active_key"
        )
        assert await cursor.fetchall() == [("recent", "active")]

        cursor = await conn.execute("SELECT COUNT(*) FROM price_observations")
        assert (await cursor.fetchone())[0] == 1
        assert result.old_inactive_active_deals == 3
        assert result.expired_provider_cache == 1
        assert result.expired_scan_result_cache == 1
        assert result.expired_alert_dedupe == 1
        assert result.expired_store_cache == 1
        assert result.old_scan_runs == 1
        assert result.old_error_events == 1
        assert result.old_autoscan_reports == 1
        assert result.old_public_posts == 1
        assert result.old_public_sending_rows == 1
        assert result.old_retailer_autoscan_runs == 1
        assert result.old_dm_receipts == 1
        await conn.close()

    asyncio.run(run())


def test_feedback_targets_are_bounded_without_counting_entire_table(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await conn.execute(
            "CREATE TABLE guild_deal_feedback_targets (created_at TEXT, expires_at TEXT)"
        )
        now = datetime.now(timezone.utc)
        rows = [
            (
                (now - timedelta(minutes=index)).isoformat(),
                (now + timedelta(days=30)).isoformat(),
            )
            for index in range(620)
        ]
        await conn.executemany(
            "INSERT INTO guild_deal_feedback_targets VALUES (?, ?)",
            rows,
        )
        await conn.execute(
            "CREATE INDEX idx_targets_created ON guild_deal_feedback_targets (created_at DESC)"
        )
        await conn.commit()

        deleted = await maintenance.delete_oldest_rows_beyond_limit(
            conn,
            table="guild_deal_feedback_targets",
            timestamp_column="created_at",
            keep=500,
            limit=50,
        )
        assert deleted == 50
        cursor = await conn.execute("SELECT COUNT(*) FROM guild_deal_feedback_targets")
        assert (await cursor.fetchone())[0] == 570
        await conn.close()

    asyncio.run(run())


def test_empty_storage_maintenance_skips_operational_commit(monkeypatch) -> None:
    async def run() -> None:
        conn = EmptyMaintenanceConnection()
        db = FakeDatabase(conn)

        _install_noop_ensures(monkeypatch)
        monkeypatch.setattr(maintenance, "ensure_quota_safe_cleanup_indexes", _noop_indexes)
        monkeypatch.setattr(maintenance, "table_exists", _no_optional_table)

        result = await maintenance.run_storage_maintenance(db, cleanup_batch_size=50)

        assert result.total_changed == 0
        assert conn.commits == 0
        assert conn.executed_sql
        assert not any(sql.startswith("DELETE ") or sql.startswith("UPDATE ") for sql in conn.executed_sql)
        assert not any("COUNT(*)" in sql.upper() for sql in conn.executed_sql)

    asyncio.run(run())


def test_bounded_cleanup_never_changes_more_than_requested_rows() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await conn.execute("CREATE TABLE scratch (state TEXT)")
        await conn.executemany(
            "INSERT INTO scratch (state) VALUES ('old')",
            [() for _ in range(12)],
        )
        await conn.commit()

        updated = await maintenance.update_bounded_rows(
            conn,
            table="scratch",
            set_sql="state = 'expired'",
            where_sql="state = ?",
            params=("old",),
            limit=5,
        )
        deleted = await maintenance.delete_bounded_rows(
            conn,
            table="scratch",
            where_sql="state = ?",
            params=("expired",),
            limit=3,
        )

        assert updated == 5
        assert deleted == 3
        cursor = await conn.execute(
            "SELECT state, COUNT(*) FROM scratch GROUP BY state ORDER BY state"
        )
        assert await cursor.fetchall() == [("expired", 2), ("old", 7)]
        await conn.close()

    asyncio.run(run())


def test_bounded_rowid_alias_survives_process_mapping_with_integer_primary_key() -> None:
    async def run() -> None:
        inner = await aiosqlite.connect(":memory:")
        await inner.execute(
            "CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT)"
        )
        await inner.executemany(
            "INSERT INTO history (state) VALUES (?)",
            [("old",), ("old",), ("keep",)],
        )
        await inner.commit()
        conn = MappingConnection(inner)

        deleted = await maintenance.delete_bounded_rows(
            conn,
            table="history",
            where_sql="state = ?",
            params=("old",),
            limit=10,
        )
        await conn.commit()

        assert deleted == 2
        assert any(
            "SELECT rowid AS __sniperplug_rowid FROM history" in sql
            for sql in conn.executed_sql
        )
        cursor = await inner.execute("SELECT id, state FROM history ORDER BY id")
        assert await cursor.fetchall() == [(3, "keep")]
        await inner.close()

    asyncio.run(run())
