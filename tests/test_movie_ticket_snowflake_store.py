from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sniperplug.services.movie_ticket_drops import MovieTicketConfig
from sniperplug.services.movie_ticket_snowflake_store import (
    SnowflakeSafeMovieTicketStore,
    snowflake_text,
)
from sniperplug.storage.db import Database


GUILD_ID = 1514374173517152418
CHANNEL_ID = 1530990344404205792


def test_movie_alert_config_round_trips_large_discord_ids_exactly(tmp_path: Path) -> None:
    asyncio.run(_exercise_exact_round_trip(tmp_path / "movie-snowflakes.sqlite3"))


async def _exercise_exact_round_trip(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    store = SnowflakeSafeMovieTicketStore(db)

    try:
        await store.save_config(
            MovieTicketConfig(
                guild_id=GUILD_ID,
                alert_channel_id=CHANNEL_ID,
                enabled=True,
            )
        )

        config = await store.get_config(GUILD_ID)
        assert config.guild_id == GUILD_ID
        assert config.alert_channel_id == CHANNEL_ID
        assert config.enabled is True

        enabled = await store.list_enabled_configs()
        assert enabled == [
            MovieTicketConfig(
                guild_id=GUILD_ID,
                alert_channel_id=CHANNEL_ID,
                enabled=True,
            )
        ]

        conn = db.require_conn()
        cursor = await conn.execute(
            "SELECT typeof(guild_id) AS guild_type, typeof(alert_channel_id) AS channel_type FROM movie_ticket_config_v2"
        )
        row = await cursor.fetchone()
        assert row["guild_type"] == "text"
        assert row["channel_type"] == "text"
    finally:
        await db.close()


def test_legacy_integer_config_is_not_reused_as_a_guessed_channel(tmp_path: Path) -> None:
    asyncio.run(_exercise_legacy_isolation(tmp_path / "movie-legacy.sqlite3"))


async def _exercise_legacy_isolation(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    store = SnowflakeSafeMovieTicketStore(db)

    try:
        await store.ensure_schema()
        conn = db.require_conn()
        await conn.execute(
            """
            INSERT INTO movie_ticket_config (guild_id, alert_channel_id, enabled, created_at, updated_at)
            VALUES (?, ?, 1, 'old', 'old')
            """,
            (GUILD_ID, CHANNEL_ID - 8),
        )
        await conn.commit()

        # The v2 store intentionally ignores the old numeric row. A server owner
        # must run /movies setup once so the exact selected channel is captured.
        config = await store.get_config(GUILD_ID)
        assert config.guild_id == GUILD_ID
        assert config.alert_channel_id is None
        assert config.enabled is False
    finally:
        await db.close()


def test_snowflake_text_rejects_lossy_or_non_decimal_values() -> None:
    assert snowflake_text(GUILD_ID) == str(GUILD_ID)
    assert snowflake_text(str(CHANNEL_ID)) == str(CHANNEL_ID)

    with pytest.raises(ValueError):
        snowflake_text(float(CHANNEL_ID))
    with pytest.raises(ValueError):
        snowflake_text("1.534e18")
    with pytest.raises(ValueError):
        snowflake_text(True)
    with pytest.raises(ValueError):
        snowflake_text(0)
