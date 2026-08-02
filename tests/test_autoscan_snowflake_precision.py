from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sniperplug.services import autoscan_live_guild_reconciliation as reconciliation
from sniperplug.services import ghost_guild_tombstones as tombstones
from sniperplug.services import setup_self_heal


REAL_GUILD_ID = 1514374173517152418
ROUNDED_GUILD_ID = 1514374173517152512
REAL_CHANNEL_ID = 1514374917594808392

HEALTH_SOURCE = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
RECONCILIATION_SOURCE = Path(
    "sniperplug/services/autoscan_live_guild_reconciliation.py"
).read_text(encoding="utf-8")
SELF_HEAL_SOURCE = Path("sniperplug/services/setup_self_heal.py").read_text(
    encoding="utf-8"
)
TOMBSTONE_SOURCE = Path(
    "sniperplug/services/ghost_guild_tombstones.py"
).read_text(encoding="utf-8")


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetchall(self):
        return list(self._rows)


class _SnowflakeConn:
    """Emulate a wire decoder that rounds raw numeric snowflakes.

    SQLite CAST(... AS TEXT) must make the fake return the exact decimal ID.
    Without the cast, the same stored ID is returned as the observed rounded
    value from the production logs.
    """

    def __init__(self):
        self.queries: list[str] = []

    async def execute(self, query: str, params=()):
        self.queries.append(query)
        value = str(REAL_GUILD_ID) if "CAST(guild_id AS TEXT)" in query else ROUNDED_GUILD_ID
        return _Cursor([{"guild_id": value}])

    async def commit(self):
        return None


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def require_conn(self):
        return self._conn


class _Bot:
    def __init__(self):
        self.guilds = [SimpleNamespace(id=REAL_GUILD_ID)]

    def get_guild(self, guild_id: int):
        return self.guilds[0] if int(guild_id) == REAL_GUILD_ID else None


def test_production_ghost_id_is_exact_float_rounding_of_real_guild() -> None:
    assert int(float(REAL_GUILD_ID)) == ROUNDED_GUILD_ID
    assert REAL_GUILD_ID - ROUNDED_GUILD_ID == -94
    assert REAL_GUILD_ID != ROUNDED_GUILD_ID


@pytest.mark.asyncio
async def test_scheduler_loader_preserves_exact_guild_snowflake(monkeypatch) -> None:
    conn = _SnowflakeConn()
    db = _Db(conn)
    bot = _Bot()

    monkeypatch.setattr(
        reconciliation,
        "load_ghost_tombstones",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        reconciliation,
        "get_public_alert_config",
        AsyncMock(
            return_value={
                "enabled": True,
                "retailers": ("walmart",),
                "channel_id": REAL_CHANNEL_ID,
            }
        ),
    )

    result = await reconciliation.list_live_public_alert_guilds(db, bot)

    assert [guild.guild_id for guild in result.guilds] == [REAL_GUILD_ID]
    assert result.stale_visible_ids == ()
    assert any("CAST(guild_id AS TEXT) AS guild_id" in query for query in conn.queries)


@pytest.mark.asyncio
async def test_health_membership_uses_same_real_scheduler_loader(monkeypatch) -> None:
    expected = reconciliation.LiveGuildLoadResult(
        guilds=(
            reconciliation.LiveAutoScanGuild(
                guild_id=REAL_GUILD_ID,
                channel_id=REAL_CHANNEL_ID,
            ),
        )
    )
    loader = AsyncMock(return_value=expected)
    monkeypatch.setattr(reconciliation, "list_live_public_alert_guilds", loader)

    enrolled, reason = await reconciliation.scheduler_membership_for_guild(
        object(),
        _Bot(),
        REAL_GUILD_ID,
    )

    assert enrolled is True
    assert "live scheduled autoscan set" in reason
    loader.assert_awaited_once()


@pytest.mark.asyncio
async def test_ghost_discovery_does_not_invent_rounded_ghost_id() -> None:
    conn = _SnowflakeConn()

    ghost_ids = await setup_self_heal._discover_ghost_ids(conn, {REAL_GUILD_ID})

    assert ghost_ids == set()
    assert len(conn.queries) == len(setup_self_heal.CONFIG_TABLES)
    assert all("CAST(guild_id AS TEXT) AS guild_id" in query for query in conn.queries)


@pytest.mark.asyncio
async def test_tombstone_loader_preserves_exact_snowflake(monkeypatch) -> None:
    conn = _SnowflakeConn()
    monkeypatch.setattr(
        tombstones,
        "ensure_ghost_tombstone_table",
        AsyncMock(return_value=None),
    )

    loaded = await tombstones.load_ghost_tombstones(conn)

    assert loaded == {REAL_GUILD_ID}
    assert any("CAST(guild_id AS TEXT) AS guild_id" in query for query in conn.queries)


def test_health_requires_real_scheduler_enrollment_and_labels_manual_report() -> None:
    assert "scheduler_membership_for_guild" in HEALTH_SOURCE
    assert "and scheduler_enrolled" in HEALTH_SOURCE
    assert 'name="Scheduler enrollment"' in HEALTH_SOURCE
    assert "Last scheduled execution" in HEALTH_SOURCE
    assert "Latest detailed report source" in HEALTH_SOURCE
    assert "Last manual test decision" in HEALTH_SOURCE
    assert "never counts as proof that the scheduled loop ran" in HEALTH_SOURCE


def test_all_scheduler_owned_snowflake_lists_cast_to_text() -> None:
    assert "SELECT CAST(guild_id AS TEXT) AS guild_id" in RECONCILIATION_SOURCE
    assert "SELECT DISTINCT CAST(guild_id AS TEXT) AS guild_id" in SELF_HEAL_SOURCE
    assert "SELECT CAST(guild_id AS TEXT) AS guild_id" in TOMBSTONE_SOURCE
