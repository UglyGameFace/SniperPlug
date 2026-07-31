from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.services.movie_ticket_drops import (
    ATOM_SOURCE_KEY,
    MovieTicketConfig,
    MovieTicketDrop,
    MovieTicketStore,
)
from sniperplug.storage.db import Database


def _drop(code: str, *, title: str | None = None) -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id=f"drop-{code.lower()}",
        source_key=ATOM_SOURCE_KEY,
        source_label="Official Atom Promotions Hub",
        title=title or code,
        code=code,
        classification="public_reusable",
        ticket_limit=2,
        offer_url="https://www.atomtickets.com/promotions",
        validity_text="Valid while supplies last.",
        restrictions=("One time use per customer.",),
        raw_text=f"Enter promo code {code} during checkout.",
    )


def test_movie_ticket_store_persists_config_source_state_drops_and_dedupe(tmp_path: Path) -> None:
    asyncio.run(_exercise_store(tmp_path / "movie-tickets.sqlite3"))


async def _exercise_store(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    store = MovieTicketStore(db)

    try:
        await store.ensure_schema()
        await store.save_config(MovieTicketConfig(guild_id=101, alert_channel_id=202, enabled=True))

        config = await store.get_config(101)
        assert config.guild_id == 101
        assert config.alert_channel_id == 202
        assert config.enabled is True
        assert [item.guild_id for item in await store.list_enabled_configs()] == [101]

        first = _drop("FIRSTFREE", title="First Movie")
        second = _drop("SECONDFREE", title="Second Movie")
        await store.replace_active_drops(ATOM_SOURCE_KEY, (first, second))
        assert {drop.code for drop in await store.list_active_drops()} == {"FIRSTFREE", "SECONDFREE"}

        await store.record_source_success(
            source_key=ATOM_SOURCE_KEY,
            etag='"abc"',
            last_modified="Fri, 31 Jul 2026 19:00:00 GMT",
            active_drop_count=2,
        )
        state = await store.get_source_state()
        assert state.etag == '"abc"'
        assert state.active_drop_count == 2
        assert state.last_success_at
        assert state.last_error == ""

        assert await store.reserve_delivery(guild_id=101, drop_id=first.drop_id, channel_id=202) is True
        assert await store.reserve_delivery(guild_id=101, drop_id=first.drop_id, channel_id=202) is False

        await store.mark_delivery_failed(guild_id=101, drop_id=first.drop_id, error="temporary send failure")
        assert await store.reserve_delivery(guild_id=101, drop_id=first.drop_id, channel_id=202) is True
        await store.mark_delivery_sent(
            guild_id=101,
            drop_id=first.drop_id,
            channel_id=202,
            message_id=303,
        )
        assert await store.reserve_delivery(guild_id=101, drop_id=first.drop_id, channel_id=202) is False
        assert await store.count_sent_for_guild(101) == 1

        await store.replace_active_drops(ATOM_SOURCE_KEY, (second,))
        remaining = await store.list_active_drops()
        assert [drop.code for drop in remaining] == ["SECONDFREE"]

        await store.record_source_error(ATOM_SOURCE_KEY, "official page unavailable")
        error_state = await store.get_source_state()
        assert error_state.last_error == "official page unavailable"
        assert error_state.active_drop_count == 2
    finally:
        await db.close()
