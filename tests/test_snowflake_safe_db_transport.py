from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.ghost_guild_tombstones import (
    clear_live_ghost_tombstones,
    mark_ghost_tombstones,
)
from sniperplug.services.public_alert_config import (
    get_public_alert_config,
    set_public_alert_config,
)
from sniperplug.services.snowflake_safe_ghost_cleanup import (
    delete_ghost_rows_once,
    remaining_ghost_ids,
)


REAL_GUILD_ID = 1514374173517152418
ROUNDED_GUILD_ID = 1514374173517152512
REAL_CHANNEL_ID = 1514374917594808392


class _Cursor:
    def __init__(self, rows=(), *, rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _PrecisionTrapConnection:
    """Fail whenever application code transports a large snowflake as int."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.config_row = None

    async def execute(self, sql: str, params=()):
        params = tuple(params)
        for value in params:
            if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
                raise AssertionError(
                    f"unsafe numeric snowflake transport would round {value} to {int(float(value))}"
                )
        self.calls.append((sql, params))
        if "SELECT enabled, retailers_json, channel_id" in sql:
            return _Cursor([self.config_row] if self.config_row else [])
        return _Cursor()

    async def commit(self):
        return None


class _Database:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_snowflake_text_preserves_every_digit() -> None:
    assert snowflake_text(REAL_GUILD_ID) == str(REAL_GUILD_ID)
    assert int(float(REAL_GUILD_ID)) == ROUNDED_GUILD_ID
    assert snowflake_text(REAL_GUILD_ID) != str(ROUNDED_GUILD_ID)


def test_tombstone_mark_and_live_clear_use_exact_text_parameters() -> None:
    async def run() -> None:
        conn = _PrecisionTrapConnection()
        await mark_ghost_tombstones(conn, [ROUNDED_GUILD_ID])
        await clear_live_ghost_tombstones(conn, [REAL_GUILD_ID])

        insert = next(call for call in conn.calls if "INSERT INTO guild_setup_ghost_tombstones" in call[0])
        delete = next(call for call in conn.calls if "DELETE FROM guild_setup_ghost_tombstones" in call[0])
        assert insert[1][0] == str(ROUNDED_GUILD_ID)
        assert delete[1] == (str(REAL_GUILD_ID),)
        assert "CAST(guild_id AS TEXT) = ?" in delete[0]

    asyncio.run(run())


def test_ghost_delete_and_verification_use_exact_text_parameters() -> None:
    async def run() -> None:
        conn = _PrecisionTrapConnection()
        failures = await delete_ghost_rows_once(conn, {ROUNDED_GUILD_ID})
        remaining = await remaining_ghost_ids(conn, {ROUNDED_GUILD_ID})

        assert failures == []
        assert remaining == set()
        guild_calls = [
            (sql, params)
            for sql, params in conn.calls
            if params and "guild_id" in sql
        ]
        assert guild_calls
        assert all(params == (str(ROUNDED_GUILD_ID),) for _sql, params in guild_calls)
        assert all("CAST(guild_id AS TEXT) = ?" in sql for sql, _params in guild_calls)

    asyncio.run(run())


def test_public_alert_config_reads_and_writes_exact_guild_text() -> None:
    async def run() -> None:
        conn = _PrecisionTrapConnection()
        db = _Database(conn)

        empty = await get_public_alert_config(db, REAL_GUILD_ID)
        assert empty["enabled"] is False
        await set_public_alert_config(
            db,
            guild_id=REAL_GUILD_ID,
            enabled=True,
            retailers=("walmart",),
            channel_id=REAL_CHANNEL_ID,
        )

        read = next(call for call in conn.calls if "SELECT enabled, retailers_json" in call[0])
        inserts = [call for call in conn.calls if "INSERT INTO guild_public_alert_settings" in call[0]]
        assert read[1] == (str(REAL_GUILD_ID),)
        assert "CAST(guild_id AS TEXT) = ?" in read[0]
        assert inserts
        assert all(call[1][0] == str(REAL_GUILD_ID) for call in inserts)
        assert any(f"ch:{REAL_CHANNEL_ID}" in call[1] for call in inserts)

    asyncio.run(run())
